.PHONY: help up down reset-db app app-stop app-logs psql health smoke \
        exp1 exp1b exp1c exp2 exp2-safe exp3 exp3-pilot exp3-noop exp3-nearcap exp4 exp4-ext exp1c-n50 pin unpin \
        eps-table exp-eps-scale exp-eps-round exp-isolation exp-lock-waits demo-locks exp4-kill opendp exp-opendp mid-demo watch clean \
        demo-breach demo-txn demo-safe demo-crash report run-report summary bundle figures docs

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
EXP1C_N      ?= 20
EXP1C_DELAYS ?= 0 5 25 100
EXP1C_OUT    ?= results/exp1c_race_window_vs_delay.csv

exp1c:         ## Experiment 1c -- widen the race window at fixed N (plateau mechanism)
	@rm -f $(EXP1C_OUT)
	@for d in $(EXP1C_DELAYS); do \
	  echo "\n=== NAIVE_RACE_DELAY_MS=$$d ==="; \
	  NAIVE_RACE_DELAY_MS=$$d docker compose up -d app >/dev/null 2>&1 || exit 1; \
	  for i in $$(seq 1 60); do curl -sf $(BASE)/health >/dev/null && break || sleep 1; done; \
	  $(PY) experiments/harness_asyncio.py --strategy naive \
	    --cap 10 --seed-spent 6 --cost 2 --workers $(EXP1C_N) --runs 10 \
	    --append --out $(EXP1C_OUT) || exit 1; \
	done
	@echo "\nrestoring NAIVE_RACE_DELAY_MS=0"
	@NAIVE_RACE_DELAY_MS=0 docker compose up -d app >/dev/null 2>&1
	@for i in $$(seq 1 60); do curl -sf $(BASE)/health >/dev/null && break || sleep 1; done
	@curl -s $(BASE)/config; echo

exp2:          ## Experiment 2 -- verify safety (atomic) under escalating contention
	$(PY) experiments/harness_asyncio.py --strategy atomic \
	  --cap 10 --seed-spent 6 --cost 2 --workers 2,3,5,10,20,50,100 --runs 25 \
	  --out results/exp2_atomic_vs_n.csv

# Experiment 2 for every safe strategy, so "safety holds across all safe
# strategies at the heaviest tested concurrency" is a measured claim. Same seed,
# load and run count as exp2; one CSV, one process per strategy (--append).
exp2-safe:     ## Experiment 2 across atomic, for_update and serializable -> one CSV
	@rm -f results/exp2_safe_strategies_vs_n.csv
	@for s in atomic for_update serializable; do \
	  $(PY) experiments/harness_asyncio.py --strategy $$s \
	    --cap 10 --seed-spent 6 --cost 2 --workers 2,3,5,10,20,50,100 --runs 25 \
	    --append --out results/exp2_safe_strategies_vs_n.csv || exit 1; \
	done

# Experiment 3 -- the cost sweep. Closed loop over /run, synthetic operation,
# one summary row per (strategy, N, duration, rep) cell. See exp3_sweep.py for
# the method. The full grids are overnight runs: --resume picks up where a
# crashed or interrupted sweep stopped, so re-running the target is safe.
EXP3_HARNESS_CPUS ?= 10,11
EXP3 = taskset -c $(EXP3_HARNESS_CPUS) $(PY) experiments/exp3_sweep.py
PINNED = docker compose -f docker-compose.yml -f docker-compose.pinned.yml

pin:           ## pin db and app to their cores (docker-compose.pinned.yml) for Exp 3
	$(PINNED) up -d
	@for i in $$(seq 1 60); do curl -sf $(BASE)/health >/dev/null && break || sleep 1; done
	@docker inspect -f '{{.Name}} cpuset={{.HostConfig.CpusetCpus}}' $$(docker compose ps -q)

unpin:         ## back to the unpinned containers every other experiment uses
	docker compose up -d
	@for i in $$(seq 1 60); do curl -sf $(BASE)/health >/dev/null && break || sleep 1; done
	@docker inspect -f '{{.Name}} cpuset={{.HostConfig.CpusetCpus}}' $$(docker compose ps -q)

exp3-pilot:    ## Experiment 3 pilot -- every strategy, N in {1,8,64}, both durations, 1 rep (~25 min)
	$(EXP3) --endpoint noop --ns 1,8,64 --reps 1 --resume --out results/exp3_pilot_noop.csv
	$(EXP3) --ns 1,8,64 --reps 1 --resume --out results/exp3_pilot.csv

exp3-noop:     ## Experiment 3 baseline -- the /noop ceiling at every N
	$(EXP3) --endpoint noop --resume --out results/exp3_noop.csv

exp3:          ## Experiment 3 -- full grid, unconstrained regime (overnight)
	$(EXP3) --regime unconstrained --resume --raw results/exp3_raw \
	  --out results/exp3_cost_sweep.csv

exp3-nearcap:  ## Experiment 3 -- full grid, near-cap regime (overnight)
	$(EXP3) --regime near_cap --resume --out results/exp3_nearcap.csv

# --- Phase 4: the three points the mid-sem panel raised -------------------

eps-table:     ## epsilon selection -- OpenDP cost and accuracy per spec -> results/eps_table.csv
	$(PY) experiments/eps_table.py

# Same contention pattern as Exp 1/2 at a tenth of the scale: cap 1, spent 0.6,
# a count at Laplace scale 5 costs 0.2 -> exactly 2 fit. The cap's units do
# not change the race or the fix.
exp-eps-scale: ## epsilon selection -- naive vs atomic at cap 1, OpenDP eps 0.2
	@rm -f results/exp_eps_scale.csv
	@for s in naive atomic; do \
	  $(PY) experiments/harness_asyncio.py --strategy $$s --adapter opendp \
	    --spec-kind count --spec-scale 5 --cap 1 --seed-spent 0.6 \
	    --workers 2,5,10,50 --runs 15 --append --out results/exp_eps_scale.csv || exit 1; \
	done

# OpenDP prices this sum at 2.0000000186; the ledger rounds UP to 2.000001, so
# two no longer fit in the 4 of headroom. Expect exactly 1 grant per run.
exp-eps-round: ## epsilon selection -- conservative rounding costs a grant (expect 1, not 2)
	$(PY) experiments/harness_asyncio.py --strategy atomic --adapter opendp \
	  --spec-kind sum --spec-scale 50 --cap 10 --seed-spent 6 \
	  --workers 5,20 --runs 10 --out results/exp_eps_round.csv

# naive and atomic at every isolation level. The service is recreated per level
# (LEDGER_ISOLATION is read at startup and asserted); the trap puts it back to
# read_committed even if a run fails, because leaving it changed would silently
# alter every later experiment.
exp-isolation: ## PostgreSQL locking -- naive/atomic at RC, RR, SERIALIZABLE -> one CSV
	@rm -f results/exp_isolation_matrix.csv
	@trap 'echo "\nrestoring LEDGER_ISOLATION=read_committed"; \
	       LEDGER_ISOLATION=read_committed docker compose up -d app >/dev/null 2>&1; \
	       for i in $$(seq 1 60); do curl -sf $(BASE)/health >/dev/null && break || sleep 1; done; \
	       curl -s $(BASE)/config | grep -o "\"isolation_level\":\"[^\"]*\""' EXIT; \
	for lvl in read_committed repeatable_read serializable; do \
	  echo "\n=== LEDGER_ISOLATION=$$lvl ==="; \
	  LEDGER_ISOLATION=$$lvl docker compose up -d app >/dev/null 2>&1 || exit 1; \
	  for i in $$(seq 1 60); do curl -sf $(BASE)/health >/dev/null && break || sleep 1; done; \
	  curl -s $(BASE)/config | grep -o '"isolation_level":"[^"]*"'; \
	  for s in naive atomic; do \
	    $(PY) experiments/harness_asyncio.py --strategy $$s \
	      --cap 10 --seed-spent 6 --cost 2 --workers 10 --runs 15 \
	      --append --out results/exp_isolation_matrix.csv || exit 1; \
	  done; \
	done

exp-lock-waits: ## PostgreSQL locking -- sample backends waiting on the row lock during bursts
	$(PY) experiments/lock_sampler.py --strategies atomic,for_update --workers 50 --runs 5

demo-locks:    ## DEMO -- two sessions: one blocks on the other's row lock, then re-checks
	$(PY) experiments/demo_locks.py

# --- Phase 7: governance ---------------------------------------------------

verify-audit:  ## walk the audit hash chain, exit 1 at the first broken link
	$(PY) experiments/verify_audit.py

demo-audit:    ## DEMO -- edit a sealed audit row; verification fails at that row
	$(PY) experiments/demo_audit.py

# RBAC is off by default, because the harnesses send no key, so this restarts
# the service with demo keys and restores it however the demo ends.
DEMO_KEYS = demo-admin:demo-admin-key:admin,demo-analyst:demo-analyst-key:analyst,demo-viewer:demo-viewer-key:viewer

demo-rbac:     ## DEMO -- viewer gets 403 on reserve; actor comes from the key
	@trap 'docker compose up -d app >/dev/null 2>&1; \
	       for i in $$(seq 1 60); do curl -sf $(BASE)/health >/dev/null && break || sleep 1; done' EXIT; \
	API_KEYS='$(DEMO_KEYS)' docker compose up -d app >/dev/null 2>&1 || exit 1; \
	for i in $$(seq 1 60); do curl -sf $(BASE)/health >/dev/null && break || sleep 1; done; \
	$(PY) experiments/demo_rbac.py

# Crash points INSIDE and AFTER the transactions (app/faults.py). Needs the
# default single uvicorn worker. One CSV per mode, same check-row format as exp4.
exp4-ext:      ## Experiment 4 extended -- 5 more fault modes, 10 reps each -> results/exp4_*.csv
	@for m in kill-in-reserve kill-in-commit kill-after-commit db-kill kill-under-load; do \
	  $(PY) experiments/crash_test.py --mode $$m --runs 10 \
	    --out results/exp4_$$(echo $$m | tr - _).csv || exit 1; \
	done
	$(PY) experiments/crash_test.py --mode kill-in-reserve --strategy serializable \
	  --runs 10 --out results/exp4_kill_in_reserve_serializable.csv

# The same intervention at N=50 with finer delays, as the locking section asks.
exp1c-n50:     ## Experiment 1c at N=50, delays 0/1/5/20 ms -> results/exp1c_n50.csv
	@$(MAKE) exp1c EXP1C_N=50 EXP1C_DELAYS="0 1 5 20" EXP1C_OUT=results/exp1c_n50.csv

demo-crash:    ## DEMO -- Experiment 4 live: abort + one real kill, PASS/FAIL per check, no CSV
	$(PY) experiments/crash_test.py --mode controlled-abort --runs 5
	$(PY) experiments/crash_test.py --mode process-kill --runs 1

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
	      FROM datasets ORDER BY created_at DESC LIMIT 10;"'

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

run-report:    ## run Experiments 1, 2 and 4 fresh, then rebuild results/report.html
	@curl -sf $(BASE)/health >/dev/null || { echo "service not up -- run 'make up' first"; exit 1; }
	@$(MAKE) reset-db
	@$(MAKE) exp1
	@$(MAKE) exp2
	@$(MAKE) exp4
	@$(MAKE) exp4-kill
	@$(MAKE) report
	@echo "\nreport rebuilt from fresh runs -- open results/report.html"

report:        ## regenerate results/report.html -- problem, solution and graph per experiment
	$(PY) experiments/report.py

summary:       ## regenerate results/summary.html -- one-page evidence sheet, exp 1-4
	$(PY) experiments/summary.py

figures:       ## regenerate results/*.png -- figures for slides and the dissertation
	$(PY) experiments/figures.py

bundle:        ## regenerate bundle-for-chat.md -- whole project as one file
	$(PY) experiments/bundle.py

docs:          ## all generated documents and figures
	@$(MAKE) report
	@$(MAKE) summary
	@$(MAKE) figures
	@$(MAKE) bundle

clean:         ## remove generated results
	rm -f results/*.csv results/*.png
