#!/usr/bin/env bash
# Entry point for the trading-btst systemd unit: the BTST desk (run_btst.py),
# page on port 9000. Paper or live is .btst_mode (repo root, "live" or
# anything else = paper), switched from the page while no trade is open.
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
exec venv/bin/python run_btst.py --port 9000
