"""DP-SGD calibration: how long does a real training job run, and what does it cost?

Experiment 3 sets its long operation with the synthetic adapter. This run measures
a real one, so that length can be compared with something other than a guess. It
is one DP-SGD training job on MNIST, CPU only, through Opacus. It feeds no
Experiment 3 cell and never touches the ledger.

The settings are the ones in the Opacus MNIST example, so the result can be
checked against a published reference: 15 epochs, lr 0.25, noise multiplier 1.3,
clipping norm 1.5, batch 240, delta 1e-5. Epsilon comes from the RDP accountant.

One row per epoch, written as it finishes. `train_s` is training only, and `cum_s`
adds up what a job that stopped at that epoch would have held a reservation for.

    ../.venv-opacus/bin/python experiments/calibrate_dpsgd.py
"""

import argparse
import csv
import os
import time
from pathlib import Path

import opacus
import torch
import torch.nn as nn
import torch.nn.functional as F
import torchvision
from opacus import PrivacyEngine
from torchvision import datasets, transforms

ROOT = Path(__file__).resolve().parents[1]
MEAN, STD = 0.1307, 0.3081  # MNIST
COLUMNS = ["rep", "epoch", "train_s", "cum_s", "epsilon", "delta", "test_accuracy",
           "noise_multiplier", "max_grad_norm", "lr", "batch_size", "train_examples",
           "torch_threads", "cpu_count", "torch", "opacus"]


class SampleConvNet(nn.Module):
    """The model from the Opacus MNIST example."""

    def __init__(self):
        super().__init__()
        self.conv1 = nn.Conv2d(1, 16, 8, 2, padding=3)
        self.conv2 = nn.Conv2d(16, 32, 4, 2)
        self.fc1 = nn.Linear(32 * 4 * 4, 32)
        self.fc2 = nn.Linear(32, 10)

    def forward(self, x):
        x = F.max_pool2d(F.relu(self.conv1(x)), 2, 1)
        x = F.max_pool2d(F.relu(self.conv2(x)), 2, 1)
        x = F.relu(self.fc1(x.view(-1, 32 * 4 * 4)))
        return self.fc2(x)


def accuracy(model, loader):
    model.eval()
    correct = total = 0
    with torch.no_grad():
        for x, y in loader:
            correct += (model(x).argmax(1) == y).sum().item()
            total += len(y)
    return correct / total


def run(rep, args, writer, fh):
    torch.manual_seed(args.seed + rep)
    tf = transforms.Compose([transforms.ToTensor(), transforms.Normalize((MEAN,), (STD,))])
    train = datasets.MNIST(args.data, train=True, download=True, transform=tf)
    test = datasets.MNIST(args.data, train=False, download=True, transform=tf)
    train_loader = torch.utils.data.DataLoader(train, batch_size=args.batch, num_workers=0)
    test_loader = torch.utils.data.DataLoader(test, batch_size=1024, num_workers=0)

    model = SampleConvNet()
    optimizer = torch.optim.SGD(model.parameters(), lr=args.lr, momentum=0)
    engine = PrivacyEngine(accountant="rdp")
    model, optimizer, train_loader = engine.make_private(
        module=model, optimizer=optimizer, data_loader=train_loader,
        noise_multiplier=args.sigma, max_grad_norm=args.clip,
    )
    criterion = nn.CrossEntropyLoss()
    cum = 0.0
    for epoch in range(1, args.epochs + 1):
        model.train()
        start = time.perf_counter()
        for x, y in train_loader:
            if len(y) == 0:  # Poisson sampling can draw an empty batch
                continue
            optimizer.zero_grad()
            criterion(model(x), y).backward()
            optimizer.step()
        elapsed = time.perf_counter() - start
        cum += elapsed
        eps = engine.get_epsilon(args.delta)
        acc = accuracy(model, test_loader)
        writer.writerow(dict(
            rep=rep, epoch=epoch, train_s=round(elapsed, 2), cum_s=round(cum, 2),
            epsilon=round(eps, 4), delta=args.delta, test_accuracy=round(acc, 4),
            noise_multiplier=args.sigma, max_grad_norm=args.clip, lr=args.lr,
            batch_size=args.batch, train_examples=len(train),
            torch_threads=torch.get_num_threads(), cpu_count=os.cpu_count(),
            torch=torch.__version__, opacus=opacus.__version__,
        ))
        fh.flush()
        print(f"rep {rep} epoch {epoch:>2}: {elapsed:6.1f} s  total {cum:7.1f} s  "
              f"eps {eps:.3f}  accuracy {acc:.3f}", flush=True)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--epochs", type=int, default=15)
    p.add_argument("--reps", type=int, default=1)
    p.add_argument("--lr", type=float, default=0.25)
    p.add_argument("--sigma", type=float, default=1.3)
    p.add_argument("--clip", type=float, default=1.5)
    p.add_argument("--batch", type=int, default=240)
    p.add_argument("--delta", type=float, default=1e-5)
    p.add_argument("--seed", type=int, default=3)
    p.add_argument("--data", default=str(ROOT.parent / ".data"))
    p.add_argument("--out", default=str(ROOT / "results" / "calibration_dpsgd.csv"))
    args = p.parse_args()
    with open(args.out, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        for rep in range(1, args.reps + 1):
            run(rep, args, writer, fh)
    print(f"wrote {args.out}")


if __name__ == "__main__":
    main()
