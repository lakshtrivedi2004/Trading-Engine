#!/usr/bin/env python3
"""A local LLM as the engine's analyst - never in the trading path.

    venv/bin/python tools/analyst.py review [--day 2026-10-15] [--telegram] [--no-llm]
    venv/bin/python tools/analyst.py ask "how does theta decay on expiry day?"

review  Facts first, computed here from the day's session logs, journal and
        config/events.json (no model involved): how long the engine ran, whether
        it covered the 15:00 last-half-hour entry, pace against the gate, why
        setups were refused, trades and P&L, alerts. The model only explains
        those facts in plain words; any number it writes that is not in the facts
        is flagged. Saved to research_out/reviews/<day>.md.
ask     Questions on the Zerodha Varsity knowledge graph
        (indian-market-knowledge-graph/kg/out/nodes.jsonl): the nearest nodes by
        embedding, then an answer from those nodes only, citing their ids.

Model: Qwen3-8B through Ollama (open weights, Apache-2.0, ~5 GB, CPU-only on this
laptop) and qwen3-embedding:0.6b for search. The model needs ~6 GB of RAM, so
it refuses to load while the market is open and an engine is running - an
engine OOM-killed mid-position (5 Oct) costs more than any review is worth.
"""
import argparse, ast, glob, json, os, re, subprocess, sys, urllib.request
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from arb.instruments import market_phase, now_ist  # noqa: E402

OLLAMA = os.environ.get("OLLAMA_HOST", "http://localhost:11434")
MODEL, EMBED = "qwen3:8b", "qwen3-embedding:0.6b"
KG = os.path.join(ROOT, "indian-market-knowledge-graph", "kg", "out", "nodes.jsonl")
TS = re.compile(r"^\[(\d\d:\d\d:\d\d)\]")
ALERT = re.compile(r"HALT|NAKED|ADOPTED|EXIT FAILED|slept|REFUSED|refused|Traceback|could not")


def _post(path, body, timeout=600):
    req = urllib.request.Request(OLLAMA + path, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read())


def safe_to_load(force=False):
    """None when the model may load, else the reason it may not."""
    if force:
        return None
    engine = subprocess.run(["pgrep", "-f", "run_bot.py"], capture_output=True).returncode == 0
    if engine and market_phase() == "open":
        return "an engine is running during market hours (the model needs ~6 GB)"
    avail = next((int(l.split()[1]) // 1024 for l in open("/proc/meminfo") if l.startswith("MemAvailable")), 0)
    if avail < 5600:                  # qwen3:8b q4 is 5.2 GB
        return f"only {avail} MB of RAM free"
    return None


def chat(system, user):
    # keep_alive 0: unload at once - the engine needs that RAM back.
    r = _post("/api/chat", {"model": MODEL, "stream": False, "think": False, "keep_alive": 0,
                            "options": {"temperature": 0.2, "num_ctx": 8192},
                            "messages": [{"role": "system", "content": system},
                                         {"role": "user", "content": user}]})
    return r["message"]["content"].strip()


# ── review ──────────────────────────────────────────────────────────────────
def _minutes(hms):
    h, m, _ = hms.split(":")
    return int(h) * 60 + int(m)


def facts(day):
    ymd = day.replace("-", "")
    out = {"day": day, "sessions": [], "trades": [], "alerts": [], "events": []}
    for path in sorted(glob.glob(f"{ROOT}/logs/*session_{ymd}_*.log")):
        lines = open(path, errors="replace").read().splitlines()
        stamps = [m.group(1) for m in map(TS.match, lines) if m]
        if not stamps:
            continue
        pulse = next((l for l in reversed(lines) if "ticks=" in l), "")
        rejects = {}
        if "{" in pulse:
            try:
                rejects = ast.literal_eval(pulse[pulse.index("{"):])
            except (ValueError, SyntaxError):
                pass
        pace = re.search(r"\| pace (.*?) \|", pulse)
        out["sessions"].append({
            "mode": "live" if "live_session" in path else "paper",
            "start": stamps[0][:5], "end": stamps[-1][:5],
            "minutes": _minutes(stamps[-1]) - _minutes(stamps[0]),
            "pace_reading_vs_need": pace.group(1) if pace else "",
            "top_refusals": dict(sorted(rejects.items(), key=lambda kv: -kv[1])[:6])})
        out["alerts"] += [l[:200] for l in lines if ALERT.search(l) and "getCandleData" not in l][:10]
    for path in (f"{ROOT}/journal/trades_{day}.jsonl", f"{ROOT}/journal/live/trades_{day}.jsonl"):
        if os.path.exists(path):
            for l in open(path):
                r = json.loads(l) if l.strip() else {}
                if "exit_reason" in r:
                    out["trades"].append({k: r.get(k) for k in (
                        "mode", "symbol", "entry", "exit", "exit_reason", "net_inr", "r_multiple", "playbooks")})
    ev = json.load(open(f"{ROOT}/config/events.json"))
    out["events"] = [k for k in ("rbi", "budget", "fed") if day in ev.get(k, [])]
    s = out["sessions"]
    out["ran_minutes"] = sum(x["minutes"] for x in s)
    out["covered_open_0930"] = any(x["start"] <= "09:30" for x in s)
    out["covered_lhm_1500"] = any(x["start"] <= "15:00" <= x["end"] for x in s)
    out["restarts"] = max(0, len(s) - 1)
    out["net_inr"] = round(sum(t["net_inr"] or 0 for t in out["trades"]), 2)
    # The conclusions are drawn here, not by the model.
    notes = []
    if not s:
        notes.append("the engine did not run today")
    else:
        if not out["covered_open_0930"]:
            notes.append(f"started at {s[0]['start']}, after 09:30: the morning setups were missed")
        if not out["covered_lhm_1500"]:
            notes.append("not running at 15:00: the last-half-hour strategy (about half of all "
                         "trades in the year test) could not trade")
        top = next(iter(s[-1]["top_refusals"]), "")
        if top == "regime_too_quiet":
            notes.append("most refusals were the pace gate: the indices moved slower than their "
                         "normal, so the strategy stood aside by design")
        if out["restarts"]:
            notes.append(f"{out['restarts']} restart(s) during the day")
    out["notes"] = notes
    return out


SYSTEM = """You are the after-hours analyst for an automated NIFTY/BANKNIFTY/SENSEX
option-buying engine. Explain the day to its owner in plain English, 5 short
bullets: what ran and for how long, why it did or did not trade (the refusal
counts and pace say why: 'regime_too_quiet' = the index moved slower than the
pace gate needs; 'no_setup' = no strategy pattern completed), the result, any
alert, and one OPERATIONAL suggestion (start before 09:15, keep running until
15:15, laptop on charger). The 'notes' field holds the day's conclusions: say
them. Use ONLY the facts given; never invent numbers, never suggest strategy
or parameter changes - those need a year backtest. The last-half-hour strategy
can only trade if the engine is running at 15:00."""


def review(a):
    day = a.day or now_ist().strftime("%Y-%m-%d")
    f = facts(day)
    body = json.dumps(f, indent=1, default=str)
    text, note = "", ""
    why = "--no-llm" if a.no_llm else safe_to_load(a.force)
    if why:
        note = f"(model not used: {why})"
    else:
        try:
            text = chat(SYSTEM, f"Facts for {day}:\n{body}")
            ok = set(re.findall(r"\d\d:\d\d|\d+(?:\.\d+)?", body.replace(",", "") + SYSTEM))
            bad = sorted({n for n in re.findall(r"\d\d:\d\d|\d+(?:\.\d+)?", text.replace(",", ""))
                          if n not in ok and not day.startswith(n) and n not in ("1", "2", "3", "4", "5")})
            if bad:
                note = f"(check: the model wrote numbers not in the facts: {', '.join(bad[:8])})"
        except Exception as e:
            note = f"(model unavailable: {e!r}; is `ollama serve` running?)"
    md = f"# Daily review {day}\n\n{text}\n\n{note}\n\n## Facts\n```json\n{body}\n```\n"
    os.makedirs(f"{ROOT}/research_out/reviews", exist_ok=True)
    path = f"{ROOT}/research_out/reviews/{day}.md"
    open(path, "w").write(md)
    print(md)
    print(f"saved {path}")
    if a.telegram and text:
        from arb.config import Credentials
        from arb.telegram import TelegramNotifier
        c = Credentials.load()
        TelegramNotifier(c.telegram_bot_token, c.telegram_chat_id).send(
            f"📋 <b>Daily review {day}</b>\n{text[:3500]}")


# ── ask ─────────────────────────────────────────────────────────────────────
def _embed(texts):
    return _post("/api/embed", {"model": EMBED, "input": texts, "keep_alive": 0})["embeddings"]


def ask(a):
    import numpy as np
    why = safe_to_load(a.force)
    if why:
        sys.exit(f"not now: {why}")
    nodes = [json.loads(l) for l in open(KG) if l.strip()]
    cache = os.path.join(ROOT, ".cache", "kg_embed.npy")
    if os.path.exists(cache) and np.load(cache).shape[0] == len(nodes):
        E = np.load(cache)
    else:
        E = np.array([v for k in range(0, len(nodes), 32)
                      for v in _embed([n["text"][:2000] for n in nodes[k:k + 32]])])
        np.save(cache, E)
    q = np.array(_embed([a.question])[0])
    sims = E @ q / (np.linalg.norm(E, axis=1) * np.linalg.norm(q) + 1e-9)
    top = [nodes[k] for k in np.argsort(-sims)[:a.k]]
    ctx = "\n\n".join(f"[{n['id']}] {n['text'][:1500]}" for n in top)
    print(chat("Answer only from the notes given, citing their [ids]. If the notes do not "
               "answer it, say so. These notes are textbook material (Zerodha Varsity): "
               "say plainly that a rule here is untested on this engine's data.",
               f"Notes:\n{ctx}\n\nQuestion: {a.question}"))
    print("\nsources:", ", ".join(n["id"] for n in top))


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("review"); r.add_argument("--day"); r.add_argument("--telegram", action="store_true")
    r.add_argument("--no-llm", action="store_true"); r.add_argument("--force", action="store_true")
    q = sub.add_parser("ask"); q.add_argument("question"); q.add_argument("-k", type=int, default=6)
    q.add_argument("--force", action="store_true")
    a = ap.parse_args()
    review(a) if a.cmd == "review" else ask(a)


if __name__ == "__main__":
    main()
