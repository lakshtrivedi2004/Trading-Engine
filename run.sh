#!/usr/bin/env bash
# Start the arbitrage engine and open the dashboard.
#
#   ./run.sh              intraday scalping, paper mode (the default)
#   ./run.sh arb          the old hold-to-expiry arbitrage scanner
#   ./run.sh status       connectivity and universe check, then exit
#   ./run.sh stop         stop a running engine
#   ./run.sh logs         follow the current session log
#   ./run.sh test         run the strategy test suites
#
# Anything after the command is passed through to run_bot.py, e.g.
#   ./run.sh --lots 2 --min-edge 0.1
set -euo pipefail
cd "$(dirname "$(readlink -f "$0")")"

PY=venv/bin/python
PORT=8089
LOGDIR=logs
PIDFILE=.engine.pid

red()   { printf '\033[31m%s\033[0m\n' "$*"; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }
dim()   { printf '\033[2m%s\033[0m\n' "$*"; }

[ -x "$PY" ] || { red "venv missing — run: python3 -m venv venv && venv/bin/pip install -r requirements.txt"; exit 1; }
[ -f .angel.env ] || { red ".angel.env missing — copy .angel.env.example and fill it in"; exit 1; }

running_pid() {
  if [ -f "$PIDFILE" ]; then
    local pid; pid=$(cat "$PIDFILE")
    if kill -0 "$pid" 2>/dev/null && grep -q run_bot /proc/"$pid"/cmdline 2>/dev/null; then
      echo "$pid"; return 0
    fi
    rm -f "$PIDFILE"
  fi
  pgrep -f "venv/bin/python run_bot.py" | head -n1 || true
}

open_dashboard() {
  local url="http://0.0.0.0:$PORT"
  if command -v xdg-open >/dev/null 2>&1; then (nohup xdg-open "$url" >/dev/null 2>&1 &) ; true
  elif command -v open >/dev/null 2>&1; then open "$url" || true; fi
}

cmd="${1:-start}"
case "$cmd" in
  status)  shift; exec "$PY" run_bot.py --status "$@" ;;
  test)
    "$PY" tests/test_strategies.py || exit 1
    echo
    "$PY" tests/test_intraday.py || exit 1
    echo
    "$PY" tests/test_optionflow.py || exit 1
    echo
    "$PY" tests/test_stocks.py || exit 1
    echo
    "$PY" tests/test_api.py || exit 1
    echo
    "$PY" tests/test_candles.py || exit 1
    echo
    "$PY" tests/test_playbooks.py || exit 1
    echo
    "$PY" tests/test_scalper.py || exit 1
    echo
    "$PY" tests/test_noise_breakout.py || exit 1
    echo
    "$PY" tests/test_stockspike.py || exit 1
    echo
    "$PY" tests/test_lhm_live.py || exit 1
    echo
    exec "$PY" tests/test_live.py ;;
  arb)     shift; set -- --strategy arb "$@" ;;
  logs)
    f=$(ls -t "$LOGDIR"/session_*.log 2>/dev/null | head -n1)
    [ -n "$f" ] || { red "no session logs yet"; exit 1; }
    exec tail -n 40 -f "$f" ;;
  stop)
    if pgrep -f "^venv/bin/python run_bot.py --mode live" >/dev/null 2>&1; then
      red "the running engine is LIVE - stop it with ./run.sh_real stop"; exit 1
    fi
    pid=$(running_pid)
    if [ -n "$pid" ]; then
      kill "$pid" && green "stopped engine (pid $pid)"
      for _ in 1 2 3 4 5 6 7 8 9 10; do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
      rm -f "$PIDFILE"
    else dim "engine not running"; fi
    exit 0 ;;
  start|--*) [ "$cmd" = start ] && shift || true ;;
  *) red "unknown command: $cmd"; exit 2 ;;
esac

# ── start ─────────────────────────────────────────────────────────────────────
# Never touch a LIVE engine from here: restarting it would square off real
# positions. Real money is started and stopped only by ./run.sh_real.
if pgrep -f "^venv/bin/python run_bot.py --mode live" >/dev/null 2>&1; then
  red "a LIVE engine is running - ./run.sh will not touch it. Use ./run.sh_real stop first."
  exit 1
fi
pid=$(running_pid)
if [ -n "$pid" ]; then
  dim "engine already running (pid $pid); restarting so today's state is re-read"
  # USR1, not TERM: a restart hands any open paper position to the new engine
  # (journal/open_positions.json) instead of squaring it off. Wait for the old
  # one to finish writing it before the new one reads it.
  kill -USR1 "$pid"
  for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 1; done
fi

# Refuse a live start from here. The trial is paper-only; live needs a
# deliberate, hand-typed invocation of run_bot.py itself.
for a in "$@"; do
  if [ "$a" = "live" ]; then
    red "live mode is not started from run.sh — the 30-day trial is paper only"; exit 3
  fi
done

mkdir -p "$LOGDIR"
log="$LOGDIR/session_$(date +%Y%m%d_%H%M%S).log"
nohup "$PY" run_bot.py --mode paper --port "$PORT" "$@" >"$log" 2>&1 &
echo $! > "$PIDFILE"

# Keep the machine awake for as long as the engine runs. On 29 Sep a closed lid
# suspended it from 10:20 to 10:38: no ticks, no exits, a hole in the candles.
# The lock lives in its own process tied to the engine's pid, so stop/restart
# and the pid file are unchanged, and it is released the moment the engine exits.
inhibit_ok=0
if command -v systemd-inhibit >/dev/null 2>&1; then
  epid=$(cat "$PIDFILE")
  nohup systemd-inhibit --what=sleep:idle:handle-lid-switch --mode=block \
    --who="Trading-Engine" --why="paper engine running (pid $epid)" \
    tail --pid="$epid" -f /dev/null >/dev/null 2>&1 &
  ipid=$!
  sleep 1
  kill -0 "$ipid" 2>/dev/null && inhibit_ok=1
fi

# Wait for the engine to report it is scanning, then open the browser.
for _ in $(seq 1 60); do
  if grep -q "scanning (paper)" "$log" 2>/dev/null; then break; fi
  if ! kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
    red "engine exited during startup — last lines:"; tail -n 20 "$log"; exit 1
  fi
  sleep 1
done

green "engine running (pid $(cat "$PIDFILE"))"
dim  "dashboard  http://0.0.0.0:$PORT"
dim  "log        $log"
dim  "stop       ./run.sh stop     ·  follow  ./run.sh logs"
if [ "$inhibit_ok" = 1 ]; then
  dim "sleep      blocked while the engine runs (closing the lid will not suspend)"
else
  red "could not block sleep - keep the lid open, or the session pauses when it suspends"
fi
grep -E "restored today|indices;|spot " "$log" | sed 's/^/  /' || true
open_dashboard
