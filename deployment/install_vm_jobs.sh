#!/usr/bin/env bash
# Run ONCE on the VM, as root, from the prod checkout (/root/Trading-Engine):
#   bash deployment/install_vm_jobs.sh
# Installs the AI-watch and after-close timers, then checks what they need.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
for u in trading-engine-daily.service trading-engine-daily.timer \
         trading-engine-aiwatch.service trading-engine-aiwatch.timer; do
  cp "deployment/$u" "/etc/systemd/system/$u"
done
systemctl daemon-reload
systemctl enable --now trading-engine-daily.timer trading-engine-aiwatch.timer
systemctl list-timers 'trading-engine-*' --no-pager
[ -f .ai.env ] || echo "WARNING: no .ai.env here - copy it from the laptop (GEMINI_API_KEY / OPENAI_API_KEY)"
git ls-remote --exit-code origin >/dev/null 2>&1 && echo "git remote reachable" \
  || echo "WARNING: cannot reach the git remote - vm_daily_sync.sh will not be able to push"
if [ ! -f .cache/history/NIFTY.json ] && [ ! -f .cache/history_live/NIFTY.json ]; then
  echo "shadow research needs index history: fetch 90 days now (AFTER market hours; paced, ~5 min):"
  echo "  GAP=12 venv/bin/python tools/fetch_history.py index 90"
fi
echo "test the push now with: deployment/vm_daily_sync.sh"
