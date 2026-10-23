#!/usr/bin/env bash
# After-close job ON THE VM (trading-engine-daily.timer, Mon-Fri 15:50 IST).
#
# The engine runs only on the VM (owner, 21 Oct 2026: the laptop is not run
# every day), so the day's results have to leave the VM for anyone to study
# them. This script:
#   1. writes the daily AI review (Telegram) and the shadow-research trades
#   2. copies the day's research-relevant files into the `vm-results` branch
#      (a separate, orphan branch: the code branches never carry account data)
#      and pushes it. Read it anywhere with tools/vm_results.sh.
# What is copied: the journal's trade / shadow / AI-watch files, capital, the
# review, the shadow-research trades, the engine's 1-minute index bars, and a
# digest of the session logs (entries, exits, halts, pace pulses, errors).
# Never copied: credentials (.angel.env, .ai.env), raw logs (they carry the
# machine's IP and MAC in Angel's error lines), order-book recordings (size).
set -uo pipefail
cd "$(dirname "$(readlink -f "$0")")/.."
day=$(TZ=Asia/Kolkata date +%F)
ymd=${day//-/}

venv/bin/python tools/analyst.py review --telegram >> logs/analyst_review.log 2>&1
nice -n 10 venv/bin/python tools/shadow_research.py >> logs/shadow_research.log 2>&1

wt=.vm-results
if [ ! -d "$wt/.git" ] && [ ! -f "$wt/.git" ]; then
  if git fetch -q origin vm-results 2>/dev/null; then
    git worktree add -q -B vm-results "$wt" origin/vm-results
  else
    git worktree add -q --detach "$wt"
    (cd "$wt" && git checkout -q --orphan vm-results && git rm -rqf . >/dev/null)
  fi
fi
(cd "$wt" && git pull -q --rebase origin vm-results 2>/dev/null || true)

dst="$wt/$day"
mkdir -p "$dst"
for f in journal/*"$day"*; do [ -f "$f" ] && cp -f "$f" "$dst/"; done
for f in journal/live/*"$day"*; do [ -f "$f" ] && cp -f "$f" "$dst/live_$(basename "$f")"; done
[ -f journal/capital.json ] && cp -f journal/capital.json "$dst/capital_paper.json"
[ -f journal/live/capital_live.json ] && cp -f journal/live/capital_live.json "$dst/capital_live.json"
[ -f "research_out/reviews/$day.md" ] && cp -f "research_out/reviews/$day.md" "$dst/review.md"
for n in NIFTY BANKNIFTY SENSEX; do
  [ -f ".cache/candles/${n}_1m.json" ] && cp -f ".cache/candles/${n}_1m.json" "$dst/"
done
grep -ahE "ENTRY|EXIT|HALT|trailed|index stop|target|ticks=|last_half_hour|in play|nothing passes|\[capital\]|\[shadow\]|Traceback|loop error|REFUSED|NAKED|ADOPTED|realised|trades  " \
  logs/*session_"${ymd}"_*.log 2>/dev/null | grep -av "X-ClientPublicIP\|X-MACAddress" > "$dst/log_digest.txt"

cd "$wt"
git add -A
if ! git diff --cached --quiet; then
  git -c user.name="trading-engine VM" -c user.email="vm@localhost" commit -qm "VM results $day"
fi
git push -q origin vm-results
