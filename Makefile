.PHONY: help up down reset-db app app-stop app-logs psql health smoke \
        exp1 exp1b exp1c exp2 exp4 exp4-kill opendp exp-opendp mid-demo watch clean \
        demo-breach demo-txn demo-safe report summary bundle docs

BASE ?= http://localhost:8000
PY   ?= .venv/bin/python

help:
	@grep -E '^[a-z0-9-]+:.*?## ' $(MAKEFILE_LIST) | \
	  awk 'BEGIN{FS=":.*?## "}{printf "  %-14s %s\n", $$1, $$2}'

# --- environment ----------------------------------------------------------

up:            ## start postgres (named volume) and the service
	docker compose up -d
	@echo "waiting for the service..."
	@for i in $$(seq 1 60); do curl -sf $(BASE)/health >/dev/null && break || sleep 1; done
	@curl -s $(BASE)/config; echo

db:            ## start postgres only (use with `make app` for the host path)
	docker compose up -d db

down:          ## stop everything, keep the volume
	docker compose down

app:           ## run the service on the host, supervised (auto-restarts after a crash)
	scripts/run_app.sh start

app-stop:      ## stop the host service
	scripts/run_app.sh stop

app-logs:      ## follow the host service log
	scripts/run_app.sh logs

reset-db:      ## wipe all ledger data (needed after a breach run re-enables the CHECK)
	docker compose exec -T db psql -U postgres -d ledger -q \
	  -c "DELETE FROM spend_records; DELETE FROM datasets;"
	@echo "ledger cleared"

psql:          ## open a psql shell on the ledger
	docker compose exec db psql -U postgres -d ledger

health:        ## service health + which mode it is in
	@curl -s $(BASE)/health; echo
	@curl -s $(BASE)/config; echo

# --- experiments ----------------------------------------------------------

exp1:          ## Experiment 1 -- reproduce the breach (naive), breach rate vs N
	$(PY) experiments/harness_asyncio.py --strategy naive \
	  --cap 10 --seed-spent 6 --cost 2 --workers 2,3,5,10,20,50 --runs 15 \
	  --out results/exp1_naive_vs_n.csv

demo-breach:   ## DEMO -- the breach, with a visual budget bar and stale-read trace
	$(PY) experiments/harness_asyncio.py --strategy naive --demo \
	  --cap 10 --seed-spent 6 --cost 2 --workers 5 --runs 1

demo-safe:     ## DEMO -- identical load on the atomic strategy, cap holds
	$(PY) experiments/harness_asyncio.py --strategy atomic --demo \
	  --cap 10 --seed-spent 6 --cost 2 --workers 5 --runs 1

exp1b:         ## Experiment 1b -- naive inside ONE transaction; still breaches
	$(PY) experiments/harness_asyncio.py --strategy naive_txn \
	  --cap 10 --seed-spent 6 --cost 2 --workers 2,3,5,10,20,50 --runs 15 \
	  --out results/exp1b_naive_txn_vs_n.csv

demo-txn:      ## DEMO -- one transaction, no lock: the cap still breaks
	$(PY) experiments/harness_asyncio.py --strategy naive_txn --demo \
	  --cap 10 --seed-spent 6 --cost 2 --workers 5 --runs 1

exp1-control:  ## control -- the SAME naive code run sequentially must not breach
	$(PY) experiments/harness_asyncio.py --strategy naive --sequential \
	  --cap 10 --seed-spent 6 --cost 2 --workers 5,20 --runs 10 \
	  --out results/exp1_naive_sequential_control.csv

# The intervention behind the overshoot plateau. Experiment 1 sweeps N with the
# race window left at whatever the machine gives it; this holds N fixed and
# widens the window itself. NAIVE_RACE_DELAY_MS is a *server* setting read from
# /config, so each point is a container recreate plus its own harness process,
# appending to one CSV. Restores the delay to 0 at the end -- leaving it set
# would silently contaminate every later experiment.
exp1c:         ## Experiment 1c -- widen the race window at fixed N (plateau mechanism)
	@rm -f results/exp1c_race_window_vs_delay.csv
	@for d in 0 5 25 100; do \
	  echo "\n=== NAIVE_RACE_DELAY_MS=$$d ==="; \
	  NAIVE_RACE_DELAY_MS=$$d docker compose up -d app >/dev/null 2>&1 || exit 1; \
	  for i in $$(seq 1 60); do curl -sf $(BASE)/health >/dev/null && break || sleep 1; done; \
	  $(PY) experiments/harness_asyncio.py --strategy naive \
	    --cap 10 --seed-spent 6 --cost 2 --workers 20 --runs 10 \
	    --append --out results/exp1c_race_window_vs_delay.csv || exit 1; \
	done
	@echo "\nrestoring NAIVE_RACE_DELAY_MS=0"
	@NAIVE_RACE_DELAY_MS=0 docker compose up -d app >/dev/null 2>&1
	@for i in $$(seq 1 60); do curl -sf $(BASE)/health >/dev/null && break || sleep 1; done
	@curl -s $(BASE)/config; echo

exp2:          ## Experiment 2 -- verify safety (atomic) under escalating contention
	$(PY) experiments/harness_asyncio.py --strategy atomic \
	  --cap 10 --seed-spent 6 --cost 2 --workers 2,3,5,10,20,50,100 --runs 25 \
	  --out results/exp2_atomic_vs_n.csv

exp4:          ## Experiment 4 -- controlled abort between reserve and commit
	$(PY) experiments/crash_test.py --mode controlled-abort --runs 30 \
	  --out results/exp4_controlled_abort.csv

exp4-kill:     ## Experiment 4 -- real process kill in the reserve->commit gap
	$(PY) experiments/crash_test.py --mode process-kill --runs 3 \
	  --out results/exp4_process_kill.csv

opendp:        ## real OpenDP query -> real epsilon -> reserved, committed, enforced
	$(PY) experiments/opendp_demo.py

# The same evidence as `opendp`, but recorded. `opendp` proves the path on
# screen and leaves nothing behind; this writes rows whose epsilon_source is
# `opendp` rather than `passed_in`, so the real-epsilon claim survives the demo.
# The cost is NOT passed in: the harness asks /adapters/epsilon for the price of
# the spec first, and `count, scale 0.5` is deterministically eps 2.0 -- the same
# cost exp2 hard-codes, against the same cap and seed. So this is exp2's
# scenario with the price supplied by OpenDP, and directly comparable to it.
exp-opendp:    ## real OpenDP epsilon under contention -> CSV (durable real-eps evidence)
	$(PY) experiments/harness_asyncio.py --strategy atomic \
	  --adapter opendp --spec-kind count --spec-scale 0.5 \
	  --cap 10 --seed-spent 6 --workers 5,20 --runs 10 \
	  --out results/exp_opendp_atomic.csv

# --- demo -----------------------------------------------------------------

watch:         ## live ledger view for the second terminal during the demo
	watch -n0.2 'docker compose exec -T db psql -U postgres -d ledger \
	  -c "SELECT name, epsilon_cap AS cap, epsilon_spent AS spent, \
	      epsilon_reserved AS reserved, epsilon_spent+epsilon_reserved AS total \
	      FROM datasets ORDER BY created_at DESC LIMIT 5;"'

mid-demo:      ## the full mid-term evidence run, in order
	@$(MAKE) reset-db
	@$(MAKE) opendp
	@$(MAKE) exp-opendp
	@$(MAKE) exp1
	@$(MAKE) exp1b
	@$(MAKE) exp1c
	@$(MAKE) exp1-control
	@$(MAKE) exp2
	@$(MAKE) exp4
	@$(MAKE) exp4-kill
	@$(MAKE) docs
	@echo "\nmid-term evidence complete -- CSVs in results/"

report:        ## regenerate results/report.html from the CSVs (the argued version)
	$(PY) experiments/report.py

summary:       ## regenerate results/summary.html -- one-page evidence sheet, exp 1-4
	$(PY) experiments/summary.py

bundle:        ## regenerate bundle-for-chat.md -- whole project as one file
	$(PY) experiments/bundle.py

docs:          ## all three generated documents
	@$(MAKE) report
	@$(MAKE) summary
	@$(MAKE) bundle

clean:         ## remove generated results
	rm -f results/*.csv
