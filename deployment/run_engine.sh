#!/usr/bin/env bash
# Single entry point for the trading-engine systemd unit. Which mode actually
# runs - paper or live - is not a property of the unit; it is whatever
# .trading_mode (repo root) says, so switching mode is "rewrite that file,
# restart this one unit" instead of stopping one systemd unit and starting a
# different one. See switch_mode.sh, which does exactly that.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."

mode=$(cat .trading_mode 2>/dev/null || true)
[ "$mode" = "live" ] || mode=paper   # missing/unrecognised state defaults safe

args=(run_bot.py --mode "$mode" --port 8090)
[ "$mode" = "live" ] && args+=(--yes)

exec venv/bin/python "${args[@]}"
