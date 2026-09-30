#!/usr/bin/env bash
# Dev counterpart of switch_mode.sh: switch the trading-engine-dev unit's mode
# (paper <-> live). Same mechanics - rewrite .trading_mode in the dev checkout,
# SIGTERM the unit (square off, no handover), then start it again.
set -euo pipefail

target="${1:-}"
case "$target" in
  paper|live) : ;;
  *) echo "usage: switch_mode_dev.sh paper|live" >&2; exit 2 ;;
esac

cd "$(dirname "$(readlink -f "$0")")/.."
echo "$target" > .trading_mode

if systemctl is-active --quiet trading-engine-dev; then
  systemctl kill --signal=SIGTERM trading-engine-dev
  for _ in $(seq 1 40); do
    systemctl is-active --quiet trading-engine-dev || break
    sleep 1
  done
fi

systemctl start trading-engine-dev
