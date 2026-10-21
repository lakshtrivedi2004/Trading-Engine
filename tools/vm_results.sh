#!/usr/bin/env bash
# Read the VM's daily results on any machine: fetches the vm-results branch
# (pushed by deployment/vm_daily_sync.sh on the VM) into ./.vm-results.
#   tools/vm_results.sh            # then: ls .vm-results/<day>/
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
git fetch -q origin vm-results
if [ -e .vm-results ]; then
  (cd .vm-results && git checkout -q --detach origin/vm-results)
else
  git worktree add -q --detach .vm-results origin/vm-results
fi
ls .vm-results | tail -10
