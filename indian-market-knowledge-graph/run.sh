#!/usr/bin/env bash
# Scalper launcher.
#
#   ./run.sh paper        live market data, simulated fills (no orders) + dashboard
#   ./run.sh live         REAL ORDERS + dashboard (asks you to type LIVE)
#   ./run.sh backtest     walk-forward test on Angel 1-min history (no orders)
#   ./run.sh dashboard    dashboard only (foreground)
#   ./run.sh stop-dashboard  stop the background dashboard
#   ./run.sh sim          offline smoke test on synthetic data
#   ./run.sh diag         read-only account check: funds, today's orders + rejection reasons
#   ./run.sh test         run the test suite
#
# Extra args are passed through, e.g.  ./run.sh paper --symbols SBIN,ICICIBANK
# Env: PORT=8050 (dashboard), NO_DASH=1 (skip dashboard), NO_WAIT=1 (don't wait for 09:00 IST),
#      SKIP_IP_CHECK=1 (live: don't verify STATIC_IP)
set -euo pipefail

cd "$(dirname "$0")"
MODE="${1:-}"
[[ $# -gt 0 ]] && shift
PORT="${PORT:-8050}"
PY="${PYTHON:-python3}"

die() { echo "error: $*" >&2; exit 1; }

usage() { sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; exit 1; }

check_env() {
  [[ -f .env ]] || die ".env not found in $(pwd)"
  for k in ANGLE_ONE_API ANGLE_ONE_ID ANGLE_ONE_PIN ANGLE_ONE_TOTP; do
    grep -q "^${k}=" .env || die "$k missing in .env"
  done
  "$PY" -c "import pandas, numpy, requests, websocket" 2>/dev/null \
    || die "missing python packages: pip install pandas numpy requests websocket-client"
}

public_ip() {
  local ip=""
  for url in https://api.ipify.org https://ifconfig.me/ip https://checkip.amazonaws.com; do
    ip=$(curl -4 -s --max-time 6 "$url" | tr -d '[:space:]') || true
    [[ "$ip" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]] && { echo "$ip"; return 0; }
  done
  return 1
}

# Angel One rejects API orders that don't come from the registered static IP.
check_static_ip() {
  [[ "${SKIP_IP_CHECK:-0}" == "1" ]] && { echo "WARNING: static IP check skipped"; return; }
  local want have
  want=$(grep -E '^STATIC_IP=' .env | tail -1 | cut -d= -f2- | tr -d '[:space:]"'"'"'')
  [[ -n "$want" ]] || die "STATIC_IP missing in .env - add the IP registered in your SmartAPI app (STATIC_IP=x.x.x.x)"
  have=$(public_ip) || die "could not look up your public IP (no internet?) - not starting live. SKIP_IP_CHECK=1 to override"
  if [[ "$have" != "$want" ]]; then
    echo "=============================================================="
    echo "  PUBLIC IP CHANGED - Angel One would reject the orders"
    echo "  registered (STATIC_IP in .env): $want"
    echo "  current public IP:              $have"
    echo "  -> update the IP in smartapi.angelone.in (My Apps) and in .env,"
    echo "     or reconnect so you get the registered IP back."
    echo "=============================================================="
    exit 1
  fi
  echo "static IP ok: $have"
}

# Engine needs ~1 min before 09:15 to load history and build the strategy gate.
wait_for_market() {
  [[ "${NO_WAIT:-0}" == "1" ]] && return
  local dow now
  dow=$(TZ=Asia/Kolkata date +%u)
  now=$(TZ=Asia/Kolkata date +%H%M)
  if (( dow > 5 )); then die "weekend in IST - market closed (NO_WAIT=1 to override)"; fi
  if (( 10#$now >= 1508 )); then die "after 15:08 IST - engine would flatten immediately"; fi
  if (( 10#$now < 900 )); then
    echo "waiting until 09:00 IST to start (now $(TZ=Asia/Kolkata date +%H:%M)) ... Ctrl+C to cancel"
    while (( 10#$(TZ=Asia/Kolkata date +%H%M) < 900 )); do sleep 20; done
  fi
}

# The dashboard is a shared, read-only background service: started once, reused by every paper/live
# session and never killed when one session ends (it follows whichever engine is running).
start_dashboard() {
  [[ "${NO_DASH:-0}" == "1" ]] && return
  if curl -s -o /dev/null --max-time 2 "http://127.0.0.1:${PORT}/api/engines"; then
    echo "dashboard -> http://127.0.0.1:${PORT} (already running)"
    return
  fi
  mkdir -p scalper/logs
  setsid nohup "$PY" -m scalper dashboard --port "$PORT" > scalper/logs/dashboard.out 2>&1 < /dev/null &
  sleep 0.5
  echo "dashboard -> http://127.0.0.1:${PORT}   (stop it with: ./run.sh stop-dashboard)"
}

case "$MODE" in
  paper)
    check_env; wait_for_market; start_dashboard
    "$PY" -m scalper paper "$@"
    ;;
  live)
    check_env
    check_static_ip
    echo "=============================================================="
    echo "  LIVE MODE - real orders, real money"
    echo "  capital ₹12,000 | daily stop ₹150 | static IP must be registered"
    echo "=============================================================="
    wait_for_market; start_dashboard
    "$PY" -m scalper live "$@"
    ;;
  backtest)
    check_env
    "$PY" -m scalper backtest "$@"
    ;;
  dashboard)
    exec "$PY" -m scalper dashboard --port "$PORT" "$@"
    ;;
  stop-dashboard)
    pkill -f "scalper dashboard --port ${PORT}" && echo "dashboard stopped" || echo "no dashboard running on :$PORT"
    ;;
  sim)
    "$PY" -m scalper sim "$@"
    ;;
  diag)
    check_env
    "$PY" -m scalper diag "$@"
    ;;
  test)
    "$PY" -m pytest -q tests/test_scalper.py "$@"
    ;;
  *) usage ;;
esac
