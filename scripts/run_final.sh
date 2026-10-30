#!/usr/bin/env bash
# Every experiment behind the final numbers, in one resumable run.
#
#   setsid nohup scripts/run_final.sh >> results/final_run.log 2>&1 < /dev/null &
#
# Safe to re-run after a crash or a WSL restart: finished stages are stamped in
# results/.final_stages/ and skipped, and the Exp 3 sweeps use --resume, so they
# continue from their last finished cell. Delete that directory to start over.
#
# The short re-runs go first, unpinned, on the configuration Experiments 1, 2
# and 4 have always used. Then the Exp 3 sweeps run pinned; `unpin` restores the
# default containers at the end.
set -u
cd "$(dirname "$0")/.."
STAMPS=results/.final_stages
mkdir -p "$STAMPS"

stage() {
    local name=$1; shift
    if [ -e "$STAMPS/$name" ]; then
        echo "##### skip $name (already done)"
        return 0
    fi
    echo "##### start $name $(date -u +%FT%TZ)"
    if "$@"; then
        touch "$STAMPS/$name"
        echo "##### done $name $(date -u +%FT%TZ)"
    else
        echo "##### FAILED $name $(date -u +%FT%TZ)"
        exit 1
    fi
}

docker compose up -d
for i in $(seq 1 60); do curl -sf localhost:8000/health >/dev/null && break || sleep 1; done

# Re-run on the fixed harness, so no reported number predates the fixes.
stage reset-db       make reset-db
stage exp1           make exp1
stage exp1b          make exp1b
stage exp1-control   make exp1-control
stage exp1c          make exp1c
stage exp1c-n50      make exp1c-n50
stage exp2           make exp2
stage exp2-safe      make exp2-safe
stage exp-opendp     make exp-opendp
stage exp-eps-scale  make exp-eps-scale
stage exp-eps-round  make exp-eps-round
stage eps-table      make eps-table
stage exp-isolation  make exp-isolation
stage exp-lock-waits make exp-lock-waits
stage exp4           make exp4
stage exp4-kill      make exp4-kill
stage exp4-ext       make exp4-ext

# The overnight sweeps. Not stamped until complete; --resume does the rest.
make pin
stage exp3-noop      make exp3-noop
stage exp3           make exp3
stage exp3-nearcap   make exp3-nearcap
make unpin
echo "##### FINAL RUN DONE $(date -u +%FT%TZ)"
