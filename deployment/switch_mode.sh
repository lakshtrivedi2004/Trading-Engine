#!/usr/bin/env bash
# Switch the running engine's mode (paper <-> live). Called by the dashboard's
# "Switch to PAPER/LIVE" button, run detached via `systemd-run` so it survives
# restarting the very unit it's invoked from.
#
# Restart is forced with a plain SIGTERM here, not the unit's configured
# KillSignal (SIGUSR1, the paper-restart handover that hands open positions to
# the *next process in the same mode*). A mode switch is not a same-mode
# restart, so it always squares off instead.
set -euo pipefail

target="${1:-}"
case "$target" in
  paper|live) : ;;
  *) echo "usage: switch_mode.sh paper|live" >&2; exit 2 ;;
esac

cd "$(dirname "$(readlink -f "$0")")/.."
echo "$target" > .trading_mode

if systemctl is-active --quiet trading-engine; then
  systemctl kill --signal=SIGTERM trading-engine
  # Up to 150 s, as the unit's TimeoutStopSec: a live square-off can take
  # longer than 40 s, and starting before it is done does nothing.
  for _ in $(seq 1 150); do
    systemctl is-active --quiet trading-engine || break
    sleep 1
  done
fi

systemctl start trading-engine
