#!/usr/bin/env bash
# Dev counterpart of run_engine.sh: entry point for the trading-engine-dev
# systemd unit. Same behaviour - mode is whatever .trading_mode (repo root of
# the dev checkout) says - but the dashboard listens on 8091 so it can run on
# the same host as prod (8090). See switch_mode_dev.sh.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."

mode=$(cat .trading_mode 2>/dev/null || true)
[ "$mode" = "live" ] || mode=paper   # missing/unrecognised state defaults safe

args=(run_bot.py --mode "$mode" --port 8091)
[ "$mode" = "live" ] && args+=(--yes)

exec venv/bin/python "${args[@]}"
