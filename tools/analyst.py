#!/usr/bin/env python3
"""The engine's AI analyst (Gemini / OpenAI by API key) - never in the trading path.

    venv/bin/python tools/analyst.py review [--day 2026-10-15] [--telegram] [--no-llm]
    venv/bin/python tools/analyst.py ask "how does theta decay on expiry day?"
    venv/bin/python tools/analyst.py ask --live "NIFTY broke the day low - PE or wait?"
    venv/bin/python tools/analyst.py watch [--every 15] [--once]     # market hours
    venv/bin/python tools/analyst.py score                           # any time

review  Facts first, computed here from the day's session logs, journal and
        config/events.json (no model involved): how long the engine ran, pace
        against the gate, why setups were refused, trades and P&L, alerts. The
        model only explains those facts; any number it writes that is not in the
        facts is flagged. Saved to research_out/reviews/<day>.md.
ask     Questions on the Zerodha Varsity knowledge graph
        (indian-market-knowledge-graph/kg/out/nodes.jsonl): the nearest nodes by
        keyword (TF-IDF) search, then an answer from those nodes only, citing ids.
watch   The owner's own routine - show the AI the market and the news, ask its
        view - as a measured experiment (19 Oct 2026). Every `every` minutes:
        index prices from Yahoo (technical facts computed here), headlines from
        ET Markets / Livemint / Business Standard / Google News RSS (news and
        sentiment), and the AI's bias per index for the next 30-60 minutes.
        Logged to journal/ai_watch_<day>.jsonl, with the day's 1-minute closes
        saved after the close. It places nothing: whether the AI's view is worth
        trading on cannot be backtested (no archive of what the news and the
        model said at each past minute), so it is measured forward first.
score   Every logged call against what the index then did over 30 and 60
        minutes, beside a plain momentum baseline (the sign of the last 15
        minutes) - the AI earns a place in the engine only by beating that,
        in both halves of the record.

Keys come from .ai.env (git-ignored): GEMINI_API_KEY, OPENAI_API_KEY, optional
GEMINI_MODEL / OPENAI_MODEL and AI_PROVIDERS (order tried, "gemini,openai").
"""
import argparse, ast, glob, json, math, os, re, sys, time, urllib.request
from collections import Counter
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from html import unescape

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from arb.instruments import market_phase, now_ist  # noqa: E402

IST = timezone(timedelta(hours=5, minutes=30))
KG = os.path.join(ROOT, "indian-market-knowledge-graph", "kg", "out", "nodes.jsonl")
TS = re.compile(r"^\[(\d\d:\d\d:\d\d)\]")
ALERT = re.compile(r"HALT|NAKED|ADOPTED|EXIT FAILED|slept|REFUSED|refused|Traceback|could not")
UA = {"User-Agent": "Mozilla/5.0"}


# ── models ──────────────────────────────────────────────────────────────────
def _keys():
    path = os.path.join(ROOT, ".ai.env")
    if os.path.exists(path):
        for line in open(path):
            k, _, v = line.strip().partition("=")
            if k and not k.startswith("#") and v:
                os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


def _post(url, body, headers, timeout=90):
    req = urllib.request.Request(url, data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json", **headers})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"HTTP {e.code}: {e.read()[:300].decode(errors='replace')}") from None


def _gemini(system, user, as_json):
    model = os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest")
    cfg = {"temperature": 0.2}
    if as_json:
        cfg["responseMimeType"] = "application/json"
    d = _post(f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
              {"system_instruction": {"parts": [{"text": system}]},
               "contents": [{"role": "user", "parts": [{"text": user}]}],
               "generationConfig": cfg},
              {"x-goog-api-key": os.environ["GEMINI_API_KEY"]})
    return "".join(p.get("text", "") for p in d["candidates"][0]["content"]["parts"]), model


def _openai(system, user, as_json):
    model = os.environ.get("OPENAI_MODEL", "gpt-5.4-mini")
    body = {"model": model, "instructions": system, "input": user}
    if as_json:
        body["text"] = {"format": {"type": "json_object"}}
    d = _post("https://api.openai.com/v1/responses", body,
              {"Authorization": f"Bearer {os.environ['OPENAI_API_KEY']}"})
    return "".join(c.get("text", "") for o in d["output"] if o.get("type") == "message"
                   for c in o["content"]), model


def chat(system, user, as_json=False):
    """(text, model) from the first provider that answers; RuntimeError if none."""
    _keys()
    errors = []
    for p in os.environ.get("AI_PROVIDERS", "gemini,openai").split(","):
        p = p.strip()
        fn, key = {"gemini": (_gemini, "GEMINI_API_KEY"), "openai": (_openai, "OPENAI_API_KEY")}.get(p, (None, ""))
        if fn is None or not os.environ.get(key):
            continue
        try:
            return fn(system, user, as_json)
        except Exception as e:
            errors.append(f"{p}: {e}")
    raise RuntimeError("; ".join(errors) or "no AI key in .ai.env")


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
    if a.no_llm:
        note = "(model not used: --no-llm)"
    else:
        try:
            text, model = chat(SYSTEM, f"Facts for {day}:\n{body}")
            note = f"(written by {model})"
            ok = set(re.findall(r"\d\d:\d\d|\d+(?:\.\d+)?", body.replace(",", "") + SYSTEM))
            bad = sorted({n for n in re.findall(r"\d\d:\d\d|\d+(?:\.\d+)?", text.replace(",", ""))
                          if n not in ok and not day.startswith(n) and n not in ("1", "2", "3", "4", "5")})
            if bad:
                note += f" (check: the model wrote numbers not in the facts: {', '.join(bad[:8])})"
        except Exception as e:
            note = f"(model unavailable: {e})"
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
def _words(s):
    return re.findall(r"[a-z0-9]+", s.lower())


def ask(a):
    if a.live:
        text, model = chat("You are an Indian index-options day trader's analyst. Answer the owner's "
                           "question from the LIVE data given (index technicals computed from 1-minute "
                           "prices, the latest headlines with IST times, and what his engine sees). "
                           "Give reasoning and a plan with levels; say what would prove you wrong. "
                           "Use only numbers present in the data. Your view is an opinion, not a "
                           "tested signal - say so if asked to trade on it.",
                           f"LIVE DATA:\n{json.dumps(snapshot(), indent=1)}\n\nQUESTION: {a.question}")
        print(text)
        print(f"\n({model}, live data {now_ist():%H:%M})")
        return
    nodes = [json.loads(l) for l in open(KG) if l.strip()]
    docs = [Counter(_words(f"{n.get('name', '')} {n.get('summary', '')} {n.get('text', '')[:3000]}"))
            for n in nodes]
    df = Counter(w for d in docs for w in d)
    idf = {w: math.log(len(docs) / c) for w, c in df.items()}
    q = Counter(_words(a.question))

    def score(d):
        return sum(q[w] * (1 + math.log(d[w])) * idf.get(w, 0) ** 2 for w in q if w in d) / \
            math.sqrt(sum(d.values()) + 1)
    top = [nodes[k] for k in sorted(range(len(nodes)), key=lambda k: -score(docs[k]))[:a.k]]
    ctx = "\n\n".join(f"[{n['id']}] {n['text'][:1500]}" for n in top)
    text, model = chat("Answer only from the notes given, citing their [ids]. If the notes do not "
                       "answer it, say so. These notes are textbook material (Zerodha Varsity): "
                       "say plainly that a rule here is untested on this engine's data.",
                       f"Notes:\n{ctx}\n\nQuestion: {a.question}")
    print(text)
    print(f"\nsources: {', '.join(n['id'] for n in top)}  ({model})")


# ── watch / score ───────────────────────────────────────────────────────────
SYMBOLS = {"NIFTY": "^NSEI", "BANKNIFTY": "^NSEBANK", "SENSEX": "^BSESN", "VIX": "^INDIAVIX"}
FEEDS = ["https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms",
         "https://www.livemint.com/rss/markets",
         "https://www.business-standard.com/rss/markets-106.rss",
         "https://news.google.com/rss/search?q=Nifty+OR+Sensex+OR+%22Bank+Nifty%22+when:1d"
         "&hl=en-IN&gl=IN&ceid=IN:en"]


def _get(url, timeout=20):
    with urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=timeout) as r:
        return r.read().decode(errors="replace")


def bars(symbol, rng="1d"):
    """[(ist datetime, close)] 1-minute bars from Yahoo, Nones dropped."""
    d = json.loads(_get(f"https://query1.finance.yahoo.com/v8/finance/chart/"
                        f"{urllib.request.quote(symbol)}?interval=1m&range={rng}"))["chart"]["result"][0]
    closes = d["indicators"]["quote"][0]["close"]
    return [(datetime.fromtimestamp(t, IST), c) for t, c in zip(d["timestamp"], closes) if c], \
        d["meta"].get("chartPreviousClose")


def technicals(name):
    """Facts a chart reader looks at, computed here - the model gets numbers, not a chart."""
    b, prev = bars(SYMBOLS[name])
    today = [x for x in b if x[0].date() == b[-1][0].date()]
    px = [c for _, c in today]
    last = px[-1]
    hi, lo = max(px), min(px)
    orb = px[:15]

    def chg(n):
        return round(100 * (last / px[-1 - n] - 1), 3) if len(px) > n else None
    return {"time": today[-1][0].strftime("%H:%M"), "last": round(last, 2),
            "prev_close": prev, "day_pct": round(100 * (last / prev - 1), 2) if prev else None,
            "chg_15m_pct": chg(15), "chg_60m_pct": chg(60),
            "day_high": round(hi, 2), "day_low": round(lo, 2),
            "from_high_pct": round(100 * (last / hi - 1), 3), "from_low_pct": round(100 * (last / lo - 1), 3),
            "opening_15m_range": [round(min(orb), 2), round(max(orb), 2)],
            "vs_opening_range": "above" if last > max(orb) else "below" if last < min(orb) else "inside"}


def headlines(hours=3, cap=40):
    out, seen, cut = [], set(), datetime.now(IST) - timedelta(hours=hours)
    for url in FEEDS:
        try:
            xml = _get(url)
        except Exception:
            continue
        for item in re.findall(r"<item>(.*?)</item>", xml, re.S):
            t = re.search(r"<title>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</title>", item, re.S)
            p = re.search(r"<pubDate>(?:<!\[CDATA\[)?(.*?)(?:\]\]>)?</pubDate>", item, re.S)
            if not t or not p:
                continue
            try:
                when = parsedate_to_datetime(p.group(1).strip()).astimezone(IST)
            except (TypeError, ValueError):
                continue
            title = unescape(re.sub(r"<.*?>|\]\]>", "", t.group(1))).strip()
            key = title.lower()[:70]
            if when >= cut and key not in seen:
                seen.add(key)
                out.append((when, title))
    return [f"{w:%H:%M} {t}" for w, t in sorted(out, reverse=True)[:cap]]


def engine_state():
    """What the running engine sees right now, from its newest session log."""
    logs = sorted(glob.glob(f"{ROOT}/logs/*session_{now_ist():%Y%m%d}_*.log"), key=os.path.getmtime)
    if not logs:
        return {"running": False}
    lines = open(logs[-1], errors="replace").read().splitlines()
    pulse = next((l for l in reversed(lines) if "ticks=" in l), "")
    events = [l[:220] for l in lines if "[intraday]" in l and "ticks=" not in l][-6:]
    pace = re.search(r"\| pace (.*?) \|", pulse)
    head = re.search(r"trades=(\d+).*?open=(\d+) pnl=(\S+)", pulse)
    return {"running": True, "mode": "live" if "live_session" in logs[-1] else "paper",
            "last_pulse": pulse[1:9] if pulse else "",
            "pace_reading_vs_need": pace.group(1) if pace else "",
            "trades": int(head.group(1)) if head else 0, "open": int(head.group(2)) if head else 0,
            "day_pnl": head.group(3) if head else "",
            "top_refusals": (dict(list(ast.literal_eval(pulse[pulse.index("{"):]).items())[:5])
                             if "{" in pulse else {}),
            "recent_events": events}


def snapshot():
    """Everything live the AI is shown: index technicals, headlines, the engine."""
    return {"time_ist": now_ist().strftime("%Y-%m-%d %H:%M"),
            "technical": {n: technicals(n) for n in SYMBOLS},
            "news": headlines(), "engine": engine_state()}


WATCH = """You are an Indian index-options day trader's analyst. From the TECHNICAL
facts (computed from 1-minute index prices) and the latest NEWS headlines (with
IST times), give your view of the direction of NIFTY, BANKNIFTY and SENSEX over
the NEXT 30-60 MINUTES. Weigh fundamentals and sentiment in the news (results,
RBI/Fed, FII flows, global cues, crude, rupee) against what price is doing.
The ENGINE block is what the owner's option-buying engine sees (its pace gate
reading vs what it needs, why it refused setups, any open trade): say whether
you agree with it standing aside or trading. "neutral" is a valid answer and
better than a guess. Reply with JSON only:
{"NIFTY": {"bias": "bullish|bearish|neutral", "confidence": 0-100, "why": "one line"},
 "BANKNIFTY": {...}, "SENSEX": {...},
 "news_sentiment": "positive|negative|mixed|none", "key_news": ["up to 3 headlines that matter"],
 "engine": "one line on the engine's stance", "plan": "levels to watch and what would change your view"}"""


def watch(a):
    os.makedirs(f"{ROOT}/journal", exist_ok=True)
    while True:
        phase = market_phase()
        now = now_ist()
        day = now.strftime("%Y-%m-%d")
        if phase in ("weekend", "holiday"):
            print(f"no session today ({phase})")
            return
        if phase == "open" and "09:20" <= now.strftime("%H:%M") <= "15:15":
            rec = {"ts": now.isoformat(timespec="seconds")}
            try:
                snap = snapshot()
                rec.update(technical=snap["technical"], news=snap["news"], engine=snap["engine"])
                text, rec["model"] = chat(WATCH, json.dumps(snap, indent=1), as_json=True)
                rec["view"] = json.loads(text[text.find("{"):text.rfind("}") + 1])
                print(now.strftime("%H:%M"), {k: (v.get("bias"), v.get("confidence"))
                                             for k, v in rec["view"].items() if isinstance(v, dict)},
                      flush=True)
            except Exception as e:
                rec["error"] = repr(e)[:300]
                print(now.strftime("%H:%M"), "error:", rec["error"], flush=True)
            with open(f"{ROOT}/journal/ai_watch_{day}.jsonl", "a") as fh:
                fh.write(json.dumps(rec) + "\n")
        elif phase != "open" and now.strftime("%H:%M") >= "15:30":
            save_prices(day)
            if not a.once:
                print("market closed - prices saved, done")
            return
        if a.once:
            return
        time.sleep(60 * a.every)


def save_prices(day):
    """The day's 1-minute closes, kept beside the log (Yahoo serves 1m for ~7 days only)."""
    path = f"{ROOT}/journal/ai_watch_prices_{day}.json"
    if os.path.exists(path) or not os.path.exists(f"{ROOT}/journal/ai_watch_{day}.jsonl"):
        return
    out = {}
    for n in ("NIFTY", "BANKNIFTY", "SENSEX"):
        b, _ = bars(SYMBOLS[n])
        out[n] = {t.strftime("%H:%M"): c for t, c in b if t.strftime("%Y-%m-%d") == day}
    json.dump(out, open(path, "w"))


def score(a):
    rows = {30: [], 60: []}
    for path in sorted(glob.glob(f"{ROOT}/journal/ai_watch_2*.jsonl")):
        day = path[-16:-6]
        try:
            save_prices(day)
            px = json.load(open(f"{ROOT}/journal/ai_watch_prices_{day}.json"))
        except Exception as e:
            print(f"{day}: no prices ({e!r})")
            continue
        for line in open(path):
            r = json.loads(line)
            if "view" not in r:
                continue
            t0 = datetime.fromisoformat(r["ts"])
            for n in ("NIFTY", "BANKNIFTY", "SENSEX"):
                v, tech = r["view"].get(n) or {}, r["technical"].get(n) or {}
                p0 = px[n].get(tech.get("time", ""))
                for h in rows:
                    p1 = px[n].get((datetime.strptime(tech.get("time", "00:00"), "%H:%M")
                                    + timedelta(minutes=h)).strftime("%H:%M"))
                    if not p0 or not p1:
                        continue
                    move = 100 * (p1 / p0 - 1)
                    bias = {"bullish": 1, "bearish": -1}.get(v.get("bias"), 0)
                    mom = (tech.get("chg_15m_pct") or 0) > 0 and 1 or -1
                    rows[h].append((day, bias, int(v.get("confidence") or 0), move, mom))
    for h, xs in rows.items():
        calls = [x for x in xs if x[1]]
        if not calls:
            print(f"+{h} min: no directional calls yet")
            continue
        days = sorted({x[0] for x in calls})
        half = days[len(days) // 2] if len(days) > 1 else days[0]

        def stats(sub, side):
            if not sub:
                return "-"
            hit = 100 * sum(side(x) * x[3] > 0 for x in sub) / len(sub)
            avg = sum(side(x) * x[3] for x in sub) / len(sub)
            return f"hit {hit:4.1f}% avg {avg:+.3f}% (n={len(sub)})"
        print(f"+{h} min | AI {stats(calls, lambda x: x[1])} | momentum on the same calls "
              f"{stats(calls, lambda x: x[4])}")
        print(f"   first half  AI {stats([x for x in calls if x[0] < half], lambda x: x[1])}")
        print(f"   second half AI {stats([x for x in calls if x[0] >= half], lambda x: x[1])}")
        print(f"   confidence >= 70: AI {stats([x for x in calls if x[2] >= 70], lambda x: x[1])}")
        print(f"   ({len(days)} days, {len(xs) - len(calls)} neutral calls not scored)")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("review"); r.add_argument("--day"); r.add_argument("--telegram", action="store_true")
    r.add_argument("--no-llm", action="store_true")
    q = sub.add_parser("ask"); q.add_argument("question"); q.add_argument("-k", type=int, default=6)
    q.add_argument("--live", action="store_true", help="answer from live prices, news and the engine")
    w = sub.add_parser("watch"); w.add_argument("--every", type=float, default=15.0)
    w.add_argument("--once", action="store_true")
    sub.add_parser("score")
    a = ap.parse_args()
    {"review": review, "ask": ask, "watch": watch, "score": score}[a.cmd](a)


if __name__ == "__main__":
    main()
