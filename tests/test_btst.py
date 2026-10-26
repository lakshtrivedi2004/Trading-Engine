#!/usr/bin/env python3
"""The BTST desk (btst/, run_btst.py, services/mongo.py), on fakes: an in-memory
MongoDB under the real MongoStore, a scripted model, the paper broker on a
scripted price, a fixed clock. No network, no orders.

  1. Reading a message: markers, the model's JSON, its numbers checked against
     the text and a strict read, the strict read standing in for a dead model
  2. The contract: the nearest expiry after today, NFO vs BFO
  3. The day: 15:15-15:30 only, one BTST a day, target 2 x (3 x SENSEX)
  4. The next day: up -> target held; down <= 20% -> held, out at 15:14;
     down > 20% -> about the same amount again, target = average + 10%
  5. Metrics on close, statistics per mode, restart safety
  6. The live order path on a fake SmartAPI
  7. The log: every line "LEVEL area | text", the reasons a trade was not
     taken, the /logs page and its filters
  8. Catching the target: websocket ticks, the LTP call while they are quiet,
     the exchange's day high for a touch between two looks, 1 s passes, live
     order checks at once on a touch, DAY targets dying at the close, and a
     restart in paper picking the trade up again
  9. Live, after the close: the target as an AMO at 16:01 for the next open,
     kept through the decision, cancelled (as AMO) before averaging down and
     at 15:14; rejected at the open -> a normal target; refused -> retried
"""
import copy
import os
import sys
from datetime import date, datetime
from types import SimpleNamespace

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from arb.config import CostModel                                # noqa: E402
from arb.instruments import IST                                  # noqa: E402
from btst import signal as S, stats                              # noqa: E402
from btst.broker import PaperBtstBroker                          # noqa: E402
from btst.contracts import find_option, tick_round               # noqa: E402
from btst.strategy import BtstStrategy                           # noqa: E402
from services.mongo import MongoStore                            # noqa: E402

PASS = FAIL = 0


def check(label, cond, detail=""):
    global PASS, FAIL
    if cond:
        PASS += 1
        print(f"  PASS  {label}" + (f" | {detail}" if detail else ""))
    else:
        FAIL += 1
        print(f"  FAIL  {label}" + (f" | {detail}" if detail else ""))


def section(t):
    print(f"\n[{t}]")


# ── an in-memory MongoDB, enough of pymongo for MongoStore ───────────────────
def _get(doc, key):
    for part in key.split("."):                      # "recording.status" -> doc["recording"]["status"]
        doc = doc.get(part) if isinstance(doc, dict) else None
    return doc


def _match(doc, flt):
    for k, v in flt.items():
        val = _get(doc, k)
        if isinstance(v, dict):
            if "$in" in v and val not in v["$in"]:
                return False
            if "$nin" in v and val in v["$nin"]:
                return False
        elif val != v:
            return False
    return True


class _Cursor(list):
    def sort(self, key, direction=1):
        return _Cursor(sorted(self, key=lambda d: d.get(key) or "", reverse=direction < 0))

    def limit(self, n):
        return _Cursor(self[:n])


class _Coll:
    def __init__(self):
        self.docs = []

    def create_index(self, *a, **k):
        pass

    def insert_one(self, doc):
        self.docs.append(copy.deepcopy(doc))

    def find_one(self, flt, proj=None, sort=None):
        hits = [d for d in self.docs if _match(d, flt)]
        if sort:
            key, direction = sort[0]
            hits.sort(key=lambda d: d.get(key) or "", reverse=direction < 0)
        return copy.deepcopy(hits[0]) if hits else None

    def find(self, flt=None, proj=None):
        return _Cursor(copy.deepcopy(d) for d in self.docs if _match(d, flt or {}))

    def update_one(self, flt, upd, upsert=False):
        hit = next((d for d in self.docs if _match(d, flt)), None)
        if hit is None:
            if not upsert:
                return
            hit = dict(flt)
            self.docs.append(hit)
        for k, v in (upd.get("$set") or {}).items():
            hit[k] = copy.deepcopy(v)
        for k, v in (upd.get("$push") or {}).items():
            hit.setdefault(k, []).append(copy.deepcopy(v))


class _DB(dict):
    def __missing__(self, k):
        self[k] = _Coll()
        return self[k]


class _Client(dict):
    def __missing__(self, k):
        self[k] = _DB()
        return self[k]


# ── the scripted world ───────────────────────────────────────────────────────
SAMPLE = """#BTST TRADE ☄️☄️
NIFTY 22800 Call Buy 105++

🕯 Target   130   160   200+

Disclaimer - Only for Educational Purposes"""

MASTER = [
    {"name": "NIFTY", "instrumenttype": "OPTIDX", "symbol": "NIFTY22OCT2622800CE", "token": "1",
     "strike": "2280000.000000", "expiry": "22OCT2026", "lotsize": "65", "exch_seg": "NFO", "tick_size": "5.000000"},
    {"name": "NIFTY", "instrumenttype": "OPTIDX", "symbol": "NIFTY27OCT2622800CE", "token": "2",
     "strike": "2280000.000000", "expiry": "27OCT2026", "lotsize": "65", "exch_seg": "NFO", "tick_size": "5.000000"},
    {"name": "NIFTY", "instrumenttype": "OPTIDX", "symbol": "NIFTY03NOV2622800CE", "token": "3",
     "strike": "2280000.000000", "expiry": "03NOV2026", "lotsize": "65", "exch_seg": "NFO", "tick_size": "5.000000"},
    {"name": "NIFTY", "instrumenttype": "OPTIDX", "symbol": "NIFTY27OCT2622800PE", "token": "4",
     "strike": "2280000.000000", "expiry": "27OCT2026", "lotsize": "65", "exch_seg": "NFO", "tick_size": "5.000000"},
    {"name": "SENSEX", "instrumenttype": "OPTIDX", "symbol": "SENSEX29OCT2673000PE", "token": "9",
     "strike": "7300000.000000", "expiry": "29OCT2026", "lotsize": "20", "exch_seg": "BFO", "tick_size": "5.000000"},
]


class Model:
    def __init__(self, answer=None, boom=None):
        self.answer, self.boom, self.calls = answer, boom, 0

    def query_json(self, prompt, **k):
        self.calls += 1
        if self.boom:
            raise self.boom
        return dict(self.answer), {"model": "test", "elapsed_s": 0.1, "cost_usd": 0}


GOOD = {"is_btst_signal": True, "indices": "NIFTY", "strike_price": 22800, "side": "CE", "price": 105,
        "reason": "btst call"}

CLOCK = {"t": datetime(2026, 10, 22, 15, 16, 0, tzinfo=IST)}
PRICE = {"ltp": 105.0}


def at(y, mo, d, h, mi, s=0):
    CLOCK["t"] = datetime(y, mo, d, h, mi, s, tzinfo=IST)


def desk(model=None, cfg=None):
    store = MongoStore(client=_Client(), db_name="t")
    broker = PaperBtstBroker(lambda c: PRICE["ltp"], funds=1e9, log=lambda *_: None, today=lambda: CLOCK["t"].date())
    conf = {"lots": 1, "scan_start": "15:15", "scan_end": "15:30", "decide_at": "09:15:30", "exit_at": "15:14",
            "target_mult": {"default": 2.0, "SENSEX": 3.0}, "avg_down_loss_pct": 20.0, "avg_target_pct": 10.0,
            "markers": ["BTST", "Disclaimer"], "ai_fallback_regex": True, "ltp_poll_sec": 0, "trail_modify_gap_sec": 0}
    conf.update(cfg or {})
    logs, sent = [], []
    st = BtstStrategy(conf, store, broker, lambda: MASTER, model or Model(GOOD), "test_channel",
                      log=logs.append, now=lambda: CLOCK["t"],
                      notifier=SimpleNamespace(send=sent.append), cost_model=CostModel.load())
    return st, store, broker, logs, sent


def msg(i, text, h, mi, day=22):
    return SimpleNamespace(id=i, raw_text=text, text=text, date=datetime(2026, 10, day, h, mi, tzinfo=IST))


# ═════════════════════════════════════════════════════════════════════════════
section("1. Reading a message")
r = S.parse(SAMPLE, llm=Model(GOOD))
check("the owner's sample: a signal, read by the model and agreeing with the strict read",
      r["is_signal"] and r["parsed_by"] == "ai"
      and r["signal"] == {"indices": "NIFTY", "strike_price": 22800.0, "side": "CE", "price": 105.0}, str(r["signal"]))
check("the strict read alone gets the same", S.strict_read(SAMPLE) ==
      {"indices": "NIFTY", "strike_price": 22800.0, "side": "CE", "price": 105.0})
m = Model(GOOD)
r = S.parse("Market update: NIFTY looks strong today", llm=m)
check("no BTST + Disclaimer markers: not a signal, the model is never asked", not r["is_signal"] and m.calls == 0)
WANT = {"indices": "NIFTY", "strike_price": 22800.0, "side": "CE", "price": 105.0}
r = S.parse(SAMPLE, llm=Model(dict(GOOD, strike_price=22900)))
check("the model's strike is not in the message: its answer is dropped, the template read stands in",
      r["is_signal"] and r["parsed_by"] == "template" and r["signal"] == WANT and "22900" in r["reason"], r["reason"])
r = S.parse(SAMPLE, llm=Model(dict(GOOD, price=130)))
check("the model took a target (130) for the buy price: the template's price after 'Buy' (105) is used",
      r["is_signal"] and r["parsed_by"] == "template" and r["signal"]["price"] == 105.0 and "disagree" in r["reason"],
      r["reason"][:100])
r = S.parse(SAMPLE, llm=Model(dict(GOOD, is_btst_signal=False, reason="a result post")))
check("the model says 'not a signal' to the exact signal template: the template read stands in",
      r["is_signal"] and r["parsed_by"] == "template" and r["signal"] == WANT, r["reason"][:100])
post = "BTST Also hit 125++ High 🎯\nNifty 22850 Call hit\nDisclaimer - Only for Educational Purposes"
r = S.parse(post, llm=Model(dict(GOOD, is_btst_signal=False, reason="a result post")))
check("...but a results post is not a signal when the model says so", not r["is_signal"]
      and "not a signal" in r["reason"])
r = S.parse(SAMPLE, llm=Model(boom=RuntimeError("429 quota")))
check("the model down (error, quota, timeout): the template read stands in",
      r["is_signal"] and r["parsed_by"] == "template" and r["signal"] == WANT, r["reason"][:80])
for bad in (None, "NIFTY 22800 CE", ["x"], {"indices": "NIFTY"}, {"is_btst_signal": "yes"}):
    r = S.parse(SAMPLE, llm=Model(bad))
    if not (r["is_signal"] and r["parsed_by"] == "template" and r["signal"] == WANT):
        break
check("the model answers something that is not the JSON asked for (None, text, a list, missing keys): "
      "the template read stands in, and parse never raises", r["is_signal"] and r["parsed_by"] == "template",
      f"{bad!r}: {r['reason'][:90]}")
r = S.parse(SAMPLE, llm=None)
check("no model configured: the template read", r["is_signal"] and r["parsed_by"] == "template")
loose = "BTST view\nNIFTY 22800 CE 105\nDisclaimer"
r = S.parse(loose, llm=Model(boom=RuntimeError("timeout")))
check("not the template but one clean line, and the model down: the strict read stands in",
      r["is_signal"] and r["parsed_by"] == "regex" and r["signal"]["price"] == 105.0, r["reason"][:80])
r = S.parse(loose, llm=Model(dict(GOOD, is_btst_signal=False, reason="a view, not a trade")))
check("...but with no template the model's 'not a signal' stands", not r["is_signal"])
r = S.parse(loose, llm=Model(dict(GOOD, price=22800)))
check("...and a disagreement with no template is no trade", not r["is_signal"] and "disagree" in r["reason"])
r = S.parse(SAMPLE, llm=Model(boom=RuntimeError("429 quota")), ai_fallback_regex=False)
check("...unless ai_fallback_regex is off", not r["is_signal"])
r = S.parse(SAMPLE, llm=Model(dict(GOOD, price=130)), ai_fallback_regex=False)
check("...(off: a disagreement is no trade)", not r["is_signal"])

REAL = [
    ('2026-05-15 15:23:46', '#BTST TRADE ☄️☄️\nNIFTY 23600 Put Buy 145++\n\n🕯 Target\xa0\xa0 170\xa0\xa0 200\xa0\xa0\xa0230+\n\nDisclaimer - Only for Educational Purposes'),
    ('2026-05-18 15:26:31', '#BTST TRADE ☄️☄️\nNIFTY 23650 Call Buy 95++\n\n🕯 Target\xa0\xa0 115\xa0\xa0 140\xa0\xa0\xa0175+\n\nDisclaimer - Only for Educational Purposes'),
    ('2026-05-19 15:22:26', '#BTST TRADE ☄️☄️\nSENSEX 75200 Put Buy 465++\n\n🕯 Target\xa0\xa0 500\xa0\xa0 540\xa0\xa0\xa0580+\n\nDisclaimer - Only for Educational Purposes'),
    ('2026-05-20 15:27:54', '#BTST TRADE ☄️☄️\nSensex 75400 Put Buy 330++\n\n🕯 Target\xa0\xa0 365\xa0\xa0 405\xa0\xa0\xa0445+\n\nDisclaimer - Only for Educational Purposes'),
    ('2026-05-21 15:26:29', '#BTST TRADE ☄️☄️\nNifty 23650 Call Buy 160++\n\n🕯 Target\xa0\xa0 185\xa0\xa0 220\xa0\xa0\xa0260+\n\nDisclaimer - Only for Educational Purposes'),
    ('2026-05-22 15:19:39', '#BTST TRADE ☄️☄️\nNifty 23750 Put Buy 140++\n\n🕯 Target\xa0\xa0 165\xa0\xa0 195\xa0\xa0\xa0230+\n\nDisclaimer - Only for Educational Purposes'),
    ('2026-10-12 15:35:46', '#BTST TRADE ☄️☄️\nNIFTY 22350 Put Buy 82++\n\n🕯 Target\xa0\xa0 105  \xa0135\xa0  166+\n\nDisclaimer - Only for Educational Purposes'),
    ('2026-10-13 15:33:00', '#BTST TRADE ☄️☄️\nSENSEX 73000 CALL Buy 475++\n\n🕯 Target\xa0\xa0 505  \xa0540\xa0  580+\n\nDisclaimer - Only for Educational Purposes'),
    ('2026-10-14 15:35:03', '#BTST TRADE ☄️☄️\nNIFTY 22600 PUT Buy 125++\n\n🕯 Target\xa0\xa0 150  \xa0180\xa0  215+\n\nDisclaimer - Only for Educational Purposes'),
    ('2026-10-15 15:38:10', '#BTST TRADE ☄️☄️\nNIFTY 22950 CALL Buy 115++\n\n🕯 Target\xa0\xa0 140  \xa0170\xa0  200+\n\nDisclaimer - Only for Educational Purposes'),
    ('2026-10-16 15:39:13', '#BTST TRADE ☄️☄️\nSENSEX 74800 CALL Buy 365++\n\n🕯 Target\xa0\xa0 405  \xa0440\xa0  480+\n\nDisclaimer - Only for Educational Purposes'),
    ('2026-10-19 15:37:05', '#BTST TRADE ☄️☄️\nSENSEX 73800 Put Buy 350++\n\n🕯 Target\xa0\xa0 380  \xa0420\xa0  460+\n\nDisclaimer - Only for Educational Purposes'),
    ('2026-10-21 15:37:17', '#BTST TRADE ☄️☄️\nNIFTY 22850 CALL Buy 115++\n\n🕯 Target\xa0\xa0 140\xa0 \xa0170\xa0\xa0 200+\n\nDisclaimer - Only for Educational Purposes'),
    ('2026-10-22 15:38:35', '#BTST TRADE ☄️☄️\nNIFTY 22800 Call Buy 105++\n\n🕯 Target\xa0\xa0 130  \xa0160\xa0  200+\n\nDisclaimer - Only for Educational Purposes'),
]

reads = [(at, S.template_read(t), S.parse(t, llm=Model(boom=RuntimeError("down")))) for at, t in REAL]
check(f"all {len(REAL)} real signals from the channel (May - Oct 2026) read by the template, model down",
      all(tr and p["is_signal"] and p["signal"] == tr for _, tr, p in reads),
      next((f"{at}: {p['reason']}" for at, tr, p in reads if not (tr and p["is_signal"])), ""))
check("...e.g. 15 May: NIFTY 23600 PE @ 145; 19 May: SENSEX 75200 PE @ 465; 13 Oct: SENSEX 73000 CE @ 475",
      reads[0][1] == {"indices": "NIFTY", "strike_price": 23600.0, "side": "PE", "price": 145.0}
      and reads[2][1] == {"indices": "SENSEX", "strike_price": 75200.0, "side": "PE", "price": 465.0}
      and reads[7][1] == {"indices": "SENSEX", "strike_price": 73000.0, "side": "CE", "price": 475.0})
VARIANTS = {
    "NIFTY INTRADAY Level\nLevel for Upside\n22850 CallBuy Above-   100++":
        {"indices": "NIFTY", "strike_price": 22850.0, "side": "CE", "price": 100.0},
    "#BTST TRADE\nBANK NIFTY 51,000 PE Buy @ 320++\nTarget 360\nDisclaimer":
        {"indices": "BANKNIFTY", "strike_price": 51000.0, "side": "PE", "price": 320.0},
    "#BTST TRADE\nNIFTY 50 22800 CE 28 OCT Buy 105++\nTarget 130\nDisclaimer":
        {"indices": "NIFTY", "strike_price": 22800.0, "side": "CE", "price": 105.0},
    "#BTST TRADE ☄️\nNifty 📌 22800 📌 Call Buy 105.5++\nTarget 130\nDisclaimer":
        {"indices": "NIFTY", "strike_price": 22800.0, "side": "CE", "price": 105.5},
    "#BTST TRADE\nFIN NIFTY 23500 Put Buy 90\nTarget 120\nDisclaimer":
        {"indices": "FINNIFTY", "strike_price": 23500.0, "side": "PE", "price": 90.0},
}
bad = {t: S.strict_read(t) for t, want in VARIANTS.items() if S.strict_read(t) != want}
check("variants read: 'CallBuy' with the index a line above, BANK NIFTY, 51,000, NIFTY 50, a date before 'Buy', "
      "emojis between, FIN NIFTY", not bad, str(bad))
NOT = ["Jay Shree Ram Trader's\nDisclaimer-\nBTST is always Risky, So Trade with less Quantity",
       "Nifty 22850 Call hit 🎯", "BTST Also hit 125++ High / 10 Point Profit", "✅💰 READY FOR BTST TRADE 🍾",
       "😀1ST TRADE - (BTST) 22800 CE / 75 POINT X 130 QTY"]
check("the channel's other posts do not read as the template (disclaimer, results, 'READY FOR BTST TRADE')",
      not any(S.template_read(t) for t in NOT), str([t for t in NOT if S.template_read(t)]))
put = SAMPLE.replace("NIFTY 22800 Call Buy 105", "SENSEX 73000 PUT buy 210")
check("PUT on SENSEX reads too", S.strict_read(put) ==
      {"indices": "SENSEX", "strike_price": 73000.0, "side": "PE", "price": 210.0})

section("1b. The scan runs to 15:40; a signal after the 15:30 close is saved, not traded")
at(2026, 10, 22, 15, 35, 50)
PRICE["ltp"] = 106.0
stL2, storeL2, brokerL2, logsL2, sentL2 = desk(cfg={"scan_end": "15:40", "market_close": "15:30"})
stL2.on_message(msg(50, SAMPLE, 15, 35))
stL2.step()
jL2 = storeL2.recent_jobs()
check("a signal posted 15:35 (scan to 15:40) is read and saved as a signal",
      storeL2.message_seen("test_channel", 50) and storeL2.recent_messages(5)[0]["is_signal"])
check("...but not traded: the day's job is 'rejected - after the market closed', nothing bought, Telegram told",
      len(jL2) == 1 and jL2[0]["status"] == "rejected" and "after the market closed" in jL2[0]["reject_reason"]
      and brokerL2.n == 0 and not storeL2.open_job() and any("NOT traded" in x for x in sentL2),
      str(jL2[:1] and jL2[0].get("reject_reason")))
at(2026, 10, 22, 15, 41)
stL2.on_message(msg(51, SAMPLE.replace("22800", "22900"), 15, 41))
stL2.step()
check("a message at 15:41 is not read; no 'no signal' day recorded (the day has its job)",
      not storeL2.message_seen("test_channel", 51) and not storeL2.day_recorded("2026-10-22"))

at(2026, 10, 22, 15, 36, 20)
stL3, storeL3, brokerL3, _, _ = desk(cfg={"scan_end": "16:00", "market_close": "15:45"})
stL3.on_message(msg(52, SAMPLE, 15, 36))
stL3.step()
jL3 = storeL3.open_job()
check("orders until 15:45 (the new hours): a signal at 15:36 is BOUGHT", jL3 and jL3["status"] == "open"
      and brokerL3.n >= 1, str(storeL3.recent_jobs()[:1] and storeL3.recent_jobs()[0].get("reject_reason")))
at(2026, 10, 22, 15, 44)
PRICE["ltp"] = 215.0
stL3.step()
check("...and its target is still watched until 15:45 (filled at 15:44)",
      storeL3.recent_jobs()[0]["status"] == "closed" and storeL3.recent_jobs()[0]["result"]["exit_reason"] == "target")
PRICE["ltp"] = 106.0
at(2026, 10, 22, 15, 46)
stL4, storeL4, brokerL4, _, sentL4 = desk(cfg={"scan_end": "16:00", "market_close": "15:45"})
stL4.on_message(msg(53, SAMPLE, 15, 45))
stL4.step()
check("a signal read at 15:46 is saved, not traded", storeL4.recent_jobs()[0]["status"] == "rejected"
      and brokerL4.n == 0 and "15:45" in storeL4.recent_jobs()[0]["reject_reason"])

section("2. The contract")
c = find_option(MASTER, "NIFTY", 22800, "CE", date(2026, 10, 22))
check("the nearest expiry AFTER today (today's 22 Oct contract settles tonight)",
      c and c["symbol"] == "NIFTY27OCT2622800CE" and c["exchange"] == "NFO" and c["lot_size"] == 65 and c["tick"] == 0.05,
      str(c))
s = find_option(MASTER, "SENSEX", 73000, "PE", date(2026, 10, 22))
check("SENSEX trades on BFO", s and s["exchange"] == "BFO" and s["lot_size"] == 20)
check("no such strike: None", find_option(MASTER, "NIFTY", 99999, "CE", date(2026, 10, 22)) is None)
check("a target on the tick, rounded up", tick_round(81.6667 * 1.1, 0.05) == 89.85)

section("3. The day: 15:15-15:30, one BTST a day")
at(2026, 10, 22, 15, 16)
PRICE["ltp"] = 106.0
st, store, broker, logs, sent = desk()
st.on_message(msg(1, SAMPLE, 15, 10))
st.on_message(msg(2, "BTST chat\nDisclaimer", 15, 16))
st.step()
check("a message posted before 15:15 is not read; one in the window is (and saved)",
      not store.message_seen("test_channel", 1) and store.message_seen("test_channel", 2) and not store.open_job())
st.on_message(msg(3, SAMPLE, 15, 16))
st.step()
j = store.open_job()
check("the signal at 15:16: BOUGHT one lot at market (paper fill at the LTP)",
      j and j["status"] == "open" and j["position"]["qty"] == 65 and j["position"]["avg_price"] == 106.0
      and j["contract"]["symbol"] == "NIFTY27OCT2622800CE", str((j or {}).get("position")))
check("...target 2 x the signal's price (210), a limit sell for the whole position",
      j["target_price"] == 210.0 and j["target_order"]["price_sent"] == 210.0 and j["target_order"]["qty"] == 65)
check("...buy and target orders, the message and the signal saved under the job id",
      [o["kind"] for o in j["orders"]] == ["buy", "target"] and j["message"]["msg_id"] == 3
      and j["signal"]["parsed_by"] == "ai" and j["job_id"].startswith("BTST-"))
check("...and Telegram told", any("BOUGHT" in x for x in sent))
st.on_message(msg(4, SAMPLE, 15, 20))
st.step()
check("a second signal the same day is ignored (one BTST a day)",
      len(store.recent_jobs()) == 1 and any("one BTST a day" in x for x in logs))
st.on_message(msg(5, SAMPLE, 15, 31))
st.step()
check("a message at 15:31 is not read", not store.message_seen("test_channel", 5))
logs3 = list(logs)                                               # section 7 reads the day's log

section("4a. Next day, up at the open: target held, hit")
at(2026, 10, 23, 9, 15, 40)
PRICE["ltp"] = 120.0
st.step()
j = store.open_job()
check("opened +13%: scenario hold, target stays 210, placed again for the day",
      j["decision"]["scenario"] == "hold" and j["target_price"] == 210.0
      and j["orders"][-1]["kind"] == "target_day2", str(j.get("decision")))
at(2026, 10, 23, 11, 0)
PRICE["ltp"] = 211.0
st.step()
done = store.get_job(j["job_id"])
r = done.get("result") or {}
check("the target fills: CLOSED at 210, target_hit, net = gross - exact charges",
      done["status"] == "closed" and r["exit_price"] == 210.0 and r["target_hit"]
      and abs(r["gross_pnl"] - (210 - 106) * 65) < 1e-6 and r["charges"] > 0
      and abs(r["net_pnl"] - (r["gross_pnl"] - r["charges"])) < 0.02, str(r)[:160])
check("...with the metrics: % of invested, slippage vs the signal, MFE, held hours, scenario",
      abs(r["pnl_pct"] - 100 * r["net_pnl"] / (106 * 65)) < 0.01 and r["entry_slippage_pct"] == round(100 / 105, 2)
      and r["max_ltp"] == 211.0 and r["scenario"] == "hold" and r["held_hours"] > 17, str(r)[:200])

section("4b. Next day, down 10%: held, out at 15:14")
at(2026, 10, 22, 15, 17)
PRICE["ltp"] = 100.0
st, store, broker, logs, sent = desk()
st.on_message(msg(10, SAMPLE, 15, 17))
st.step()
at(2026, 10, 23, 9, 15, 40)
PRICE["ltp"] = 90.0
st.step()
j = store.open_job()
check("opened -10% (above -25%): no top-up, the target stays 210", j["decision"]["scenario"] == "hold"
      and j["target_price"] == 210.0 and not j.get("topups"), str(j.get("decision")))
at(2026, 10, 23, 15, 14, 2)
PRICE["ltp"] = 95.0
st.step()
done = store.get_job(j["job_id"])
r = done.get("result") or {}
check("15:14: target cancelled, sold at market (95), CLOSED time_exit with a loss",
      done["status"] == "closed" and r["exit_reason"] == "time_exit" and r["exit_price"] == 95.0
      and r["net_pnl"] < 0 and [o["kind"] for o in done["orders"]][-2:] == ["target_cancel", "exit"], str(r)[:120])

section("4c. Next day, live: -25% -> +50%, target cut 25%, trailing; -50% -> +50% more, target average + 10%")
at(2026, 10, 22, 15, 18)
PRICE["ltp"] = 105.0
st, store, broker, logs, sent = desk()
st.on_message(msg(20, SAMPLE, 15, 18))
st.step()
at(2026, 10, 23, 9, 15, 5)
PRICE["ltp"] = 95.0
st.step()
j = store.open_job()
check("the open at 95 (-9.5% vs the buy 105): no top-up yet, the target 210 back in for the day",
      j["decision"]["scenario"] == "hold" and j["target_price"] == 210.0 and not j.get("topups"), str(j.get("decision")))
at(2026, 10, 23, 10, 0)
PRICE["ltp"] = 78.0
st.step()
j = store.open_job()
check("10:00 at 78 (-25.7%): +50% of Rs 6,825 -> 1 lot (65 @ 78); average 91.50; target 210 -> 157.50 (cut 25%); "
      "trailing from 78, floor 91.50 + 10% = 100.65",
      j["topped_pct"] == 50 and j["position"]["qty"] == 130 and j["position"]["avg_price"] == 91.5
      and j["target_price"] == 157.5 and j["trail"]["active"] and j["trail"]["ref"] == 78.0
      and j["trail"]["floor"] == 100.65 and j["decision"]["scenario"] == "topup_50",
      str({k: j.get(k) for k in ("topped_pct", "target_price", "trail")})[:200])
at(2026, 10, 23, 10, 30)
PRICE["ltp"] = 75.5
st.step()
check("...75.50, 2.5 points under that buy: the target 2 points lower, 155.50",
      store.open_job()["target_price"] == 155.5)
at(2026, 10, 23, 11, 0)
PRICE["ltp"] = 52.0
st.step()
j = store.open_job()
avg = round((6825 + 5070 + 3380) / 195, 2)
check(f"11:00 at 52 (-50.5%): the other 50% -> 1 lot (65 @ 52); 195 qty, average {avg}; target = average + 10% "
      f"= {tick_round(avg * 1.1)}; trailing ends",
      j["topped_pct"] == 100 and j["position"]["qty"] == 195 and j["position"]["avg_price"] == avg
      and j["target_price"] == tick_round(avg * 1.1) and not j["trail"]["active"] and len(j["topups"]) == 2
      and j["decision"]["scenario"] == "topup_100", str({k: j.get(k) for k in ("topped_pct", "target_price")}))
at(2026, 10, 23, 11, 5)
PRICE["ltp"] = 40.0
st.step()
check("...no third top-up (100% is the most)", len(store.open_job()["topups"]) == 2)
at(2026, 10, 23, 13, 0)
PRICE["ltp"] = 87.0
st.step()
done = store.get_job(j["job_id"])
r = done.get("result") or {}
check("the target fills: CLOSED in profit on 195 qty, with the top-ups in the metrics",
      done["status"] == "closed" and r["target_hit"] and r["averaged"] and r["qty"] == 195 and r["net_pnl"] > 0
      and r["topped_pct"] == 100 and r["topups"] == 2, str(r)[:160])

at(2026, 10, 22, 15, 18)
PRICE["ltp"] = 105.0
st, store, broker, logs, sent = desk()
st.on_message(msg(21, SAMPLE, 15, 18))
st.step()
at(2026, 10, 23, 9, 15, 5)
PRICE["ltp"] = 50.0
st.step()
j = store.open_job()
avg = round((6825 + 6500) / 195, 2)
check(f"a gap-down open at 50 (-52%): 100% at once -> 2 lots (130 @ 50, the nearest to Rs 6,825); average {avg}; "
      f"target = average + 10% = {tick_round(avg * 1.1)}; no trailing",
      j["topped_pct"] == 100 and j["position"]["qty"] == 195 and j["target_price"] == tick_round(avg * 1.1)
      and not j.get("trail") and j["topups"][0]["step"] == "full", str({k: j.get(k) for k in ("topped_pct", "target_price")}))

section("4d. SENSEX: target 3 x")
at(2026, 10, 22, 15, 19)
PRICE["ltp"] = 210.0
st, store, broker, logs, sent = desk(model=Model({"is_btst_signal": True, "indices": "SENSEX",
                                                  "strike_price": 73000, "side": "PE", "price": 210, "reason": "x"}))
st.on_message(msg(30, put, 15, 19))
st.step()
j = store.open_job()
check("SENSEX 73000 PE @ 210: 1 lot (20) on BFO, target 630", j and j["position"]["qty"] == 20
      and j["contract"]["exchange"] == "BFO" and j["target_price"] == 630.0, str((j or {}).get("target_price")))

section("5. Restart safety and statistics")
st2, store2, _, logs2, sent2 = desk()
store2.insert_job({"job_id": "BTST-x", "status": "entering", "trade_date": "2026-10-22",
                   "created_at": "2026-10-22T15:16:00", "contract": {"symbol": "NIFTY27OCT2622800CE"}})
st2.resume()
check("a job left 'entering' by a crash is not retried: needs_review, and said",
      store2.get_job("BTST-x")["status"] == "needs_review" and any("not retried" in x for x in sent2))
jobs = [{"status": "closed", "mode": "paper", "trade_date": "2026-10-20", "result": {"net_pnl": 1000, "pnl_pct": 10,
         "invested": 10000, "target_hit": True, "exit_reason": "target", "charges": 60}},
        {"status": "closed", "mode": "paper", "trade_date": "2026-10-21", "result": {"net_pnl": -400, "pnl_pct": -4,
         "invested": 10000, "exit_reason": "time_exit", "averaged": True, "charges": 70}},
        {"status": "closed", "mode": "live", "trade_date": "2026-10-22", "result": {"net_pnl": 300, "pnl_pct": 3,
         "invested": 9000, "exit_reason": "time_exit", "charges": 50}},
        {"status": "rejected", "mode": "paper", "trade_date": "2026-10-23", "reject_reason": "no LTP"}]
p = stats.summary(jobs, "paper")
check("statistics per mode: paper 2 trades, 50% win, net Rs 600, PF 2.5, 1 target / 1 time exit, 1 averaged",
      p["trades"] == 2 and p["win_rate"] == 50.0 and p["net_pnl"] == 600 and p["profit_factor"] == 2.5
      and p["target_hits"] == 1 and p["time_exits"] == 1 and p["averaged"] == 1 and p["open_or_failed"] == 1, str(p))
check("...live kept apart", stats.summary(jobs, "live")["trades"] == 1 and stats.summary(jobs, "live")["net_pnl"] == 300)
rows = stats.by_day(jobs)
check("every day listed, newest first, with why a day was not traded",
      [x["date"] for x in rows] == ["2026-10-23", "2026-10-22", "2026-10-21", "2026-10-20"] and rows[0]["reason"] == "no LTP")

section("5b. No signal, no trade - and the day is still listed")
at(2026, 10, 22, 15, 20)
stq, storeq, brokerq, logsq, sentq = desk(model=Model(dict(GOOD, is_btst_signal=False, reason="a result post")))
stq.on_message(msg(40, "BTST Also hit 125++ High 🎯\nDisclaimer - Only for Educational Purposes", 15, 18))
stq.on_message(msg(41, "Good evening all", 15, 19))
stq.step()
check("15:20, only a non-signal message: nothing bought, no day record yet (the window is open)",
      not storeq.recent_jobs() and not storeq.day_recorded("2026-10-22") and brokerq.n == 0)
at(2026, 10, 22, 15, 30, 5)
stq.step()
stq.step()
d = storeq.days.find_one({"date": "2026-10-22"})
check("after 15:30: 'no signal - no trade' saved once, with the messages read",
      d and d["status"] == "no_signal" and d["messages_read"] == 2 and len(storeq.recent_days()) == 1
      and brokerq.n == 0 and any("no trade" in x for x in logsq), str(d))
at(2026, 10, 23, 9, 16)
stq.step()
check("the next morning: nothing to manage, nothing sent", brokerq.n == 0 and not storeq.open_job())
at(2026, 10, 24, 15, 35)                                         # a Saturday
stq.step()
check("a weekend is not a trading day: no record", not storeq.day_recorded("2026-10-24"))
at(2026, 10, 22, 15, 31)
check("a day with a trade (or a refused signal) is not marked 'no signal'",
      (st._record_quiet_day() or True) and not store.day_recorded("2026-10-22"))
rowsq = stats.by_day(storeq.recent_jobs(), storeq.recent_days())
check("the day list shows it", rowsq and rowsq[0]["date"] == "2026-10-22"
      and rowsq[0]["status"] == "no signal - no trade" and "2 messages" in rowsq[0]["reason"], str(rowsq[:1]))

from btst import web                                             # noqa: E402
sq = web.state(stq, storeq)
check("the page counts the days with no signal", sq["no_signal_days"] == 1 and sq["days"][0]["job_id"] == "")
state = web.state(st, store)
check("the page's state: mode, the open trade, both modes' statistics, every day, the messages",
      state["mode"] == "paper" and state["open_job"] and [s["mode"] for s in state["stats"]] == ["live", "paper"]
      and len(state["days"]) == 1 and state["messages"], str(list(state)))

import run_btst                                                  # noqa: E402
dm = run_btst.DailyMaster(loader=lambda: (MASTER, "cache (1.0h old)"), today=lambda: CLOCK["t"].date())
check("the scrip master as the desk loads it: load_scrip_master's (rows, source) -> the rows",
      dm() is MASTER and find_option(dm(), "NIFTY", 22800, "CE", date(2026, 10, 22)) is not None)
at(2026, 10, 22, 15, 16)
stx, storex, _, logsx, _ = desk()
stx.master_fn = lambda: (MASTER, "cache")       # the shape that crashed entry before
stx.on_message(msg(3, SAMPLE, 15, 16))
stx.step()
jx = storex.recent_jobs()[0]
check("a contract lookup that fails rejects the job (never left 'entering', which would block the day)",
      jx["status"] == "rejected" and "contract lookup" in jx["reject_reason"] and not storex.open_job(),
      jx.get("reject_reason", "")[:90])

section("6. The live order path (AngelBtstBroker over a fake SmartAPI)")
from btst.broker import AngelBtstBroker                          # noqa: E402


class FakeAngel:
    def __init__(self, fill=True, refuse=None):
        self.fill, self.refuse, self.placed, self.cancelled, self.n, self.rows = fill, refuse, [], [], 0, {}

    def ltpData(self, ex, sym, tok):
        return {"status": True, "data": {"ltp": 100.0}}

    def placeOrderFullResponse(self, p):
        self.placed.append(dict(p))
        if self.refuse:
            return {"status": False, "message": self.refuse, "errorCode": "AG7002", "data": ""}
        self.n += 1
        uid = f"u{self.n}"
        done = p["duration"] == "IOC"
        self.rows[uid] = {"orderid": str(self.n), "uniqueorderid": uid,
                          "orderstatus": ("complete" if self.fill else "cancelled") if done else "open",
                          "filledshares": p["quantity"] if (done and self.fill) else "0",
                          "averageprice": "101.5" if (done and self.fill) else "0", "text": ""}
        return {"status": True, "data": {"orderid": str(self.n), "uniqueorderid": uid}}

    def individual_order_details(self, uid):
        return {"status": True, "data": self.rows.get(uid)}

    def orderBook(self):
        return {"status": True, "data": list(self.rows.values())}

    def cancelOrder(self, oid, variety):
        self.cancelled.append(oid)
        for r in self.rows.values():
            if r["orderid"] == oid and r["orderstatus"] == "open":
                r["orderstatus"] = "cancelled"
        return {"status": True}

    def position(self):
        return {"status": True, "data": [{"tradingsymbol": "NIFTY27OCT2622800CE", "netqty": "65"}]}


C = find_option(MASTER, "NIFTY", 22800, "CE", date(2026, 10, 22))
api = FakeAngel()
lb = AngelBtstBroker(api, protection_pct=2.5, sleep=lambda s: None, log=lambda *_: None)
b = lb.buy_market(C, 65)
p0 = api.placed[0]
check("live BUY: a CARRYFORWARD (overnight) marketable limit, IOC, LTP + 2.5% on the tick, NFO",
      p0["producttype"] == "CARRYFORWARD" and p0["ordertype"] == "LIMIT" and p0["duration"] == "IOC"
      and p0["price"] == "102.50" and p0["exchange"] == "NFO" and p0["quantity"] == "65"
      and p0["transactiontype"] == "BUY", str(p0))
check("...the fill read back from Angel (65 @ 101.5), not assumed",
      b["ok"] and b["filled"] == 65 and b["avg_price"] == 101.5 and b["status"] == "complete")
t = lb.place_limit_sell(C, 65, 210.0)
p1 = api.placed[1]
check("live target: a LIMIT DAY sell at 210, CARRYFORWARD", t["ok"] and p1["ordertype"] == "LIMIT"
      and p1["duration"] == "DAY" and p1["price"] == "210.00" and p1["transactiontype"] == "SELL", str(p1))
st_ = lb.cancel(t)
check("cancel: the order reads cancelled", st_ and st_["status"] == "cancelled" and api.cancelled)
check("the account's net quantity from the position book", lb.net_qty(C) == 65)
api2 = FakeAngel(refuse="122.1.2.3 is not a registered IP")
r2 = AngelBtstBroker(api2, sleep=lambda s: None, log=lambda *_: None).buy_market(C, 65)
check("an unregistered IP (AG7002) is a refusal, said plainly", not r2["ok"] and "not registered" in r2["error"],
      r2["error"][:90])
api3 = FakeAngel(fill=False)
r3 = AngelBtstBroker(api3, sleep=lambda s: None, log=lambda *_: None).buy_market(C, 65)
check("an IOC that did not fill: not ok, nothing assumed held", not r3["ok"] and r3["filled"] == 0)

section("7. The log")
import json as _json                                             # noqa: E402
import re as _re                                                 # noqa: E402
import tempfile                                                  # noqa: E402
import urllib.request                                            # noqa: E402
from btst.logbook import Logbook                                 # noqa: E402

tagged = _re.compile(r"^(ERROR|WARN|TRADE|INFO)\s+[\w.-]+ \| ")
check("every line the strategy logs carries a level and an area", logs3 and all(tagged.match(x) for x in logs3),
      next((x for x in logs3 if not tagged.match(x)), ""))
has = lambda *words: any(all(w in x for w in words) for x in logs3)
check("...a message before 15:15 is counted as not read (outside the window)",
      has("INFO", "message", "outside today's 15:15-15:30 window"))
check("...each message read is logged, and why it was not a signal",
      has("message", "#2 posted 15:16") and has("signal", "#2 NOT a signal"))
check("...the model's answer, the signal, the contract, the funds check and the orders",
      has("ai", "#3 model answered") and has("signal", "#3 SIGNAL (ai)") and has("contract", "NIFTY27OCT2622800CE token")
      and has("entry", "funds check") and has("TRADE", "order", "BUY NIFTY27OCT2622800CE x65 at market")
      and has("TRADE", "order", "LIMIT 210.00 DAY (target)"))
check("...the second signal's refusal is a WARN with the reason", has("WARN", "signal", "one BTST a day"))

book = Logbook(echo=False)
book.log("INFO  message | #7 posted 15:16: hello")
book.log("WARN  signal | #7 NOT a signal - no markers")
book.log("TRADE order | BUY X x65 at market")
book.log("[test_channel #9 2026-10-22 15:17] line one\nline two failed")      # the reader's plain line
book.log("[telegram-user] not logged in yet - once, by hand")
book.log("ERROR manage | error: boom\nTraceback (most recent call last):\n  File \"x.py\", line 1\nValueError: boom")
book.log("WARN  mongo | could not connect to mongodb+srv://user:s3cretpw@cluster0.x.net/?appName=y key AIzaSyA1234567890abcdefghijk")
rows, src = book.lines(limit=50)
check("the ring buffer serves the page when there is no file: newest first",
      src.startswith("memory") and len(rows) == 7 and rows[0]["area"] == "mongo" and rows[-1]["area"] == "message",
      str([(r["level"], r["area"]) for r in rows]))
check("a channel message stays one entry (its second line too) and is never a warning",
      any(r["area"] == "channel" and r["level"] == "INFO" and "line two failed" in r["text"] for r in rows))
check("a plain 'not logged in' line from the Telegram reader is a WARN under telegram",
      any(r["area"] == "telegram" and r["level"] == "WARN" for r in rows))
check("a traceback stays with its ERROR line", any(r["level"] == "ERROR" and "ValueError: boom" in r["text"]
                                                    for r in rows))
check("secrets scrubbed: the MongoDB password and a Google key never reach the log",
      "s3cretpw" not in rows[0]["text"] and "AIzaSyA1234567890" not in rows[0]["text"] and "***@" in rows[0]["text"],
      rows[0]["text"])
check("filters: problems / trades / signals / text",
      {r["level"] for r in book.lines(group="errors")[0]} <= {"ERROR", "WARN"}
      and [r["area"] for r in book.lines(group="trades")[0]] == ["order"]
      and {r["area"] for r in book.lines(group="signals")[0]} == {"message", "signal", "channel"}
      and len(book.lines(q="#7")[0]) == 2)

fd, path = tempfile.mkstemp(suffix=".log")
with os.fdopen(fd, "w") as fh:                                   # what systemd appends: stdout and stderr
    fh.write(Logbook(echo=False).log("INFO  start | starting") + "\n")
    fh.write("Traceback (most recent call last):\n  File \"run_btst.py\", line 9, in main\nRuntimeError: no mongo\n")
    fh.write("[2026-10-21 15:20:00] INFO  message | yesterday\n")
fbook = Logbook(path=path, echo=False)
frows, fsrc = fbook.lines(limit=50)
check("from btst.log: an uncaught traceback (stderr) is one ERROR entry, its last line included",
      fsrc.endswith(".log") and len(frows) == 3 and frows[1]["level"] == "ERROR" and frows[1]["area"] == "crash"
      and frows[1]["text"].endswith("RuntimeError: no mongo"), str(frows))
check("...and the day filter keeps one day", [r["text"] for r in fbook.lines(day="2026-10-21")[0]] == ["yesterday"])
os.remove(path)

httpd = web.serve(st, store, lambda to, exit_trade=False: {"ok": False, "error": "test"}, port=0,
                  host="127.0.0.1", book=book)
base = f"http://127.0.0.1:{httpd.server_address[1]}"
get = lambda u: urllib.request.urlopen(base + u, timeout=10).read().decode()
home, page = get("/"), get("/logs")
api_l = _json.loads(get("/api/logs?group=errors&limit=100"))
st_json = _json.loads(get("/api/state"))
httpd.shutdown()
check("the desk has a View logs button opening /logs", 'href="/logs"' in home and "View logs" in home)
check("/logs is the log page, /api/logs its filtered lines with counts",
      "BTST Log" in page and api_l["source"].startswith("memory")
      and api_l["lines"] and all(r["level"] in ("ERROR", "WARN") for r in api_l["lines"])
      and api_l["counts"].get("ERROR") == 1, str(api_l)[:200])
check("the desk's state counts today's warnings and errors for the button",
      isinstance(st_json.get("log_problems_today"), int), str(st_json.get("log_problems_today")))

section("8. Catching the target")
from btst.ticks import PriceWatch, _Feed                         # noqa: E402

QUOTE = {"ltp": 150.0, "high": 160.0}
calls = {"rest": 0}


def rest_quote(c):
    calls["rest"] += 1
    return dict(QUOTE) if QUOTE.get("ltp") else None


def watch(**k):
    return PriceWatch(rest_quote=rest_quote, log=lambda *_: None, ltp_poll_sec=0, use_ws=False,
                      today=lambda: CLOCK["t"].date(), **k)


at(2026, 10, 23, 10, 0)
w = watch()
pb = PaperBtstBroker(w.price, log=lambda *_: None, watch=w, today=lambda: CLOCK["t"].date())
QUOTE.update(ltp=150.0, high=160.0)
t8 = pb.place_limit_sell(C, 65, 210.0)
QUOTE.update(ltp=180.0, high=160.0)
check("a target 210 rests while trades are below it", pb.order_state(t8)["status"] == "open" and w.peak() == 180.0)
QUOTE.update(ltp=185.0, high=212.0)          # touched 212 between two looks, back to 185 now
s8 = pb.order_state(t8)
check("a touch BETWEEN two looks is caught by the exchange's day high: filled at the target 210",
      s8["status"] == "complete" and s8["avg"] == 210.0 and w.peak() == 212.0, str(s8))

w2 = watch()
pb2 = PaperBtstBroker(w2.price, log=lambda *_: None, watch=w2, today=lambda: CLOCK["t"].date())
QUOTE.update(ltp=230.0, high=240.0)
t9 = pb2.place_limit_sell(C, 65, 210.0)
check("a target placed below the market fills at once, at the market (230), like a real limit sell",
      t9["status"] == "complete" and pb2.order_state(t9)["avg"] == 230.0)
QUOTE.update(ltp=150.0, high=245.0)          # a day high from before the mark is not a touch
w3 = watch()
w3.price(C)
w3.mark()
QUOTE.update(ltp=151.0, high=245.0)
w3.price(C)
check("a day high older than the target is not counted as a touch", w3.peak() == 151.0, str(w3.peak()))
at(2026, 10, 24, 9, 20)
check("...and a mark is for its own day (the day high resets overnight)", w3.peak() is None)

at(2026, 10, 23, 10, 0)
w4 = PriceWatch(rest_quote=rest_quote, log=lambda *_: None, ltp_poll_sec=0, use_ws=False,
                today=lambda: CLOCK["t"].date())
w4.follow(C)
feed = _Feed(w4, SimpleNamespace(), 4, log=lambda *_: None)
feed.register(C["token"])
calls["rest"] = 0
feed._on_data(None, {"token": str(C["token"]), "last_traded_price": 19875, "high_price_of_the_day": 20500,
                     "best_5_buy_data": [], "best_5_sell_data": []})
p4 = w4.price(C)
check("a websocket tick (paise) is the price, and no LTP call is made while ticks are fresh",
      p4 == 198.75 and w4.state()["source"] == "websocket" and calls["rest"] == 0, str(w4.state()))
w5 = watch()
w5.follow(C)
QUOTE.update(ltp=204.5, high=358.65)
w5.price(C)                                  # the LTP call: rupees
w5.mark()
f5 = _Feed(w5, SimpleNamespace(), 4, log=lambda *_: None)
f5.register(C["token"])
f5._on_data(None, {"token": str(C["token"]), "last_traded_price": 21785, "high_price_of_the_day": 35865,
                   "best_5_buy_data": [], "best_5_sell_data": []})
check("the same day high from the socket (paise) and the LTP call (rupees) is not a new high (VM, 23 Oct)",
      w5.peak() == 217.85, str(w5.peak()))
w4.last["at"] -= 10                          # the socket went quiet
calls["rest"] = 0
QUOTE.update(ltp=199.0, high=205.0)
check("...quiet for over ws_fresh_sec: the LTP call stands in", w4.price(C) == 199.0 and calls["rest"] == 1)


def desk8():
    store = MongoStore(client=_Client(), db_name="t8")
    pw = watch()
    br = PaperBtstBroker(pw.price, log=lambda *_: None, watch=pw, today=lambda: CLOCK["t"].date())
    conf = {"lots": 1, "scan_start": "15:15", "scan_end": "15:30", "decide_at": "09:15:30", "exit_at": "15:14",
            "target_mult": {"default": 2.0, "SENSEX": 3.0}, "avg_down_loss_pct": 20.0, "avg_target_pct": 10.0,
            "markers": ["BTST", "Disclaimer"], "ai_fallback_regex": True, "manage_poll_sec": 1, "poll_sec": 3}
    lg = []
    s_ = BtstStrategy(conf, store, br, lambda: MASTER, Model(GOOD), "test_channel", log=lg.append,
                      now=lambda: CLOCK["t"], notifier=SimpleNamespace(send=lambda *_: None),
                      cost_model=CostModel.load(), prices=pw)
    return s_, store, br, pw, lg


at(2026, 10, 22, 15, 16)
QUOTE.update(ltp=106.0, high=110.0)
s8_, st8, br8, pw8, lg8 = desk8()
s8_.on_message(msg(3, SAMPLE, 15, 16))
s8_.step()
j8 = st8.open_job()
check("after the BUY the desk passes every 1 s (manage_poll_sec), not 3", j8 and s8_.wait_sec() == 1.0,
      str(s8_.wait_sec()))
at(2026, 10, 22, 15, 20)
QUOTE.update(ltp=190.0, high=212.0)          # the same afternoon: 212 traded between passes
s8_.step()
j8 = st8.get_job(j8["job_id"])
check("the same day, a touch of the target between passes: CLOSED as a target hit at 210",
      j8["status"] == "closed" and j8["result"]["exit_reason"] == "target" and j8["result"]["exit_price"] == 210.0
      and j8["result"]["max_ltp"] >= 212.0, str(j8.get("result", {}).get("exit_reason")))
check("...said in the log", any("reached the target" in x for x in lg8))
s8_.step()
check("no trade open: back to 3 s passes, websocket stopped", s8_.wait_sec() == 3.0)

at(2026, 10, 22, 15, 16)
QUOTE.update(ltp=106.0, high=110.0)
s9, st9, br9, pw9, lg9 = desk8()
s9.on_message(msg(3, SAMPLE, 15, 16))
s9.step()
j9 = st9.open_job()
at(2026, 10, 23, 9, 15, 10)                  # next morning, before decide_at
QUOTE.update(ltp=120.0, high=125.0)
s9.step()
j9o = st9.get_job(j9["job_id"])
check("yesterday's DAY target died at the close (as at the exchange); a new one for the day at the open",
      br9.orders[j9["target_order"]["order_id"]]["status"] == "cancelled" and j9o["target_order"]["kind"] == "target_day2"
      and st9.open_job(), str(j9o.get("target_order", {}).get("kind")))
at(2026, 10, 23, 9, 16)
s9.step()                                    # the decision: hold, the target 210 placed again for today
# a restart: a new paper broker and price watch, the same MongoDB
pw9c = watch()
s9b = BtstStrategy(s9.cfg, st9, PaperBtstBroker(pw9c.price, log=lambda *_: None, watch=pw9c,
                                                today=lambda: CLOCK["t"].date()),
                   lambda: MASTER, None, "test_channel", log=lg9.append, now=lambda: CLOCK["t"], prices=pw9c)
s9b.resume()
check("after a restart in paper the open trade is adopted: position and today's target back",
      s9b.broker.net_qty(C) == 65 and st9.get_job(j9["job_id"])["target_order"]["order_id"] in s9b.broker.orders)
at(2026, 10, 23, 10, 0)
QUOTE.update(ltp=150.0, high=160.0)
s9b.step()                                   # its first look: the day high so far is the baseline
at(2026, 10, 23, 11, 0)
QUOTE.update(ltp=200.0, high=211.0)
s9b.step()
j9 = st9.get_job(j9["job_id"])
check("...and its target is still caught after the restart (CLOSED target @ 210, not 'nothing left')",
      j9["status"] == "closed" and j9["result"]["exit_reason"] == "target" and j9["result"]["exit_price"] == 210.0,
      str(j9.get("result", {}).get("exit_reason")))


class CountingLive(PaperBtstBroker):
    mode = "live"

    def __init__(self, *a, **k):
        super().__init__(*a, **k)
        self.checks = 0

    def order_state(self, order):
        self.checks += 1
        return super().order_state(order)


at(2026, 10, 22, 15, 16)
QUOTE.update(ltp=106.0, high=110.0)
sL, stL, _, pwL, lgL = desk8()
sL.broker = CountingLive(pwL.price, log=lambda *_: None, watch=pwL, today=lambda: CLOCK["t"].date())
sL.cfg["order_poll_sec"] = 60
sL.on_message(msg(3, SAMPLE, 15, 16))
sL.step()
for k in range(4):
    at(2026, 10, 22, 15, 17, k)
    QUOTE.update(ltp=120.0 + k, high=125.0)
    sL.step()
n_quiet = sL.broker.checks
at(2026, 10, 22, 15, 18)
QUOTE.update(ltp=150.0, high=211.0)
sL.step()
check("live: the order's state is not read every pass (order_poll_sec) - but at once when the price touches",
      n_quiet == 1 and sL.broker.checks == 2 and stL.recent_jobs()[0]["status"] == "closed",
      f"{n_quiet} quiet, {sL.broker.checks} total")

section("9. Live: the target as an AMO for the next open")
import run_bot                                                   # noqa: E402,F401  (live_funds)

RESTING = ("open", "after market order req received")


class Exchange:
    """A fake Angel: IOC fills at the price, DAY orders rest and die at the
    close, AMOs wait for the open; a cancel must name the order's variety."""

    def __init__(self):
        self.px, self.high, self.rows, self.n, self.net = 106.0, 110.0, {}, 0, 0
        self.held = {}                                    # net quantity per trading symbol
        self.events, self.refuse_amo = [], 0

    def ltpData(self, ex, sym, tok):
        return {"status": True, "data": {"ltp": str(self.px), "high": str(self.high)}}

    def rmsLimit(self):
        return {"status": True, "data": {"availablecash": "1000000", "net": "1000000"}}

    def position(self):
        return {"status": True, "data": [{"tradingsymbol": k, "netqty": str(v)} for k, v in self.held.items()]}

    def placeOrderFullResponse(self, p):
        self.events.append(("place", p["variety"], p["transactiontype"], p["duration"], p["quantity"], p["price"]))
        if p["variety"] == "AMO" and self.refuse_amo:
            self.refuse_amo -= 1
            return {"status": False, "message": "AMO window closed", "errorcode": "AB4036", "data": None}
        self.n += 1
        uid, q = f"u{self.n}", int(p["quantity"])
        row = {"orderid": str(self.n), "uniqueorderid": uid, "variety": p["variety"], "side": p["transactiontype"],
               "sym": p["tradingsymbol"],
               "duration": p["duration"], "qty": q, "price": float(p["price"]), "orderstatus": "open",
               "filledshares": "0", "averageprice": "0", "text": ""}
        if p["duration"] == "IOC":
            row.update(orderstatus="complete", filledshares=str(q), averageprice=str(self.px))
            d = q if p["transactiontype"] == "BUY" else -q
            self.net += d
            self.held[p["tradingsymbol"]] = self.held.get(p["tradingsymbol"], 0) + d
        elif p["variety"] == "AMO":
            row["orderstatus"] = "after market order req received"
        self.rows[uid] = row
        return {"status": True, "data": {"orderid": str(self.n), "uniqueorderid": uid}}

    def individual_order_details(self, uid):
        return {"status": True, "data": self.rows.get(uid)}

    def orderBook(self):
        return {"status": True, "data": list(self.rows.values())}

    def cancelOrder(self, oid, variety):
        self.events.append(("cancel", oid, variety))
        row = next(r for r in self.rows.values() if r["orderid"] == oid)
        if row["variety"] != variety:
            return {"status": False, "message": "Invalid variety", "data": None}
        if row["orderstatus"] in RESTING:
            row["orderstatus"] = "cancelled"
        return {"status": True, "data": {"orderid": oid}}

    def _fill(self, r, px):
        r.update(orderstatus="complete", filledshares=str(r["qty"]), averageprice=str(px))
        self.net -= r["qty"]
        self.held[r["sym"]] = self.held.get(r["sym"], 0) - r["qty"]

    def close_day(self):
        for r in self.rows.values():
            if r["variety"] == "NORMAL" and r["duration"] == "DAY" and r["orderstatus"] == "open":
                r["orderstatus"] = "cancelled"            # the exchange's close

    def open_market(self, px, reject_amo=False):
        self.px = self.high = px
        for r in self.rows.values():
            if r["variety"] == "AMO" and r["orderstatus"] == "after market order req received":
                if reject_amo:
                    r.update(orderstatus="rejected", text="price outside the operating range")
                    continue
                r["orderstatus"] = "open"
                if r["side"] == "SELL" and px >= r["price"]:
                    self._fill(r, px)                     # a sell below the opening price fills at the open

    def resting_sells(self):
        return [r for r in self.rows.values() if r["side"] == "SELL" and r["orderstatus"] in RESTING]

    def modifyOrder(self, p):
        self.events.append(("modify", p["orderid"], p["variety"], p["price"]))
        row = next(r for r in self.rows.values() if r["orderid"] == p["orderid"])
        if row["orderstatus"] not in RESTING:
            return {"status": False, "message": "order not open", "data": None}
        row["price"] = float(p["price"])
        return {"status": True, "data": {"orderid": p["orderid"]}}

    def drop_amos(self):
        for r in self.rows.values():                      # what Angel does to an AMO over a weekend / holiday
            if r["variety"] == "AMO" and r["orderstatus"] in RESTING:
                r["orderstatus"] = "cancelled"

    def tick(self, px):
        self.px = px
        for r in self.rows.values():
            if r["side"] == "SELL" and r["orderstatus"] == "open" and px >= r["price"]:
                self._fill(r, r["price"])


def live_desk(day=22, cfg=None):
    ex = Exchange()
    store = MongoStore(client=_Client(), db_name="amo")
    conf = {"lots": 1, "scan_start": "15:15", "scan_end": "15:30", "decide_at": "09:15:30", "exit_at": "15:14",
            "target_mult": {"default": 2.0, "SENSEX": 3.0}, "avg_down_loss_pct": 20.0, "avg_target_pct": 10.0,
            "markers": ["BTST", "Disclaimer"], "ai_fallback_regex": True, "ltp_poll_sec": 0, "order_poll_sec": 0,
            "amo_exit": True, "amo_at": "16:01", "amo_until": "08:55", "amo_tries": 3, "amo_retry_sec": 300}
    conf.update(cfg or {})
    lg, sent = [], []
    s_ = BtstStrategy(conf, store, AngelBtstBroker(ex, sleep=lambda s: None, log=lambda *_: None), lambda: MASTER,
                      Model(GOOD), "test_channel", log=lg.append, now=lambda: CLOCK["t"],
                      notifier=SimpleNamespace(send=sent.append), cost_model=CostModel.load())
    at(2026, 10, day, 15, 16)
    s_.on_message(msg(3, SAMPLE, 15, 16, day=day))
    s_.step()
    return s_, store, ex, lg, sent


sA, stA, exA, lgA, sentA = live_desk()
jA = stA.open_job()
at(2026, 10, 22, 15, 45)
sA.step()
check("live entry: bought, DAY target 210 at the exchange; no AMO before 16:01",
      jA and jA["mode"] == "live" and exA.net == 65 and not stA.get_job(jA["job_id"]).get("amo"))
exA.close_day()
at(2026, 10, 22, 16, 1)
sA.step()
jA = stA.get_job(jA["job_id"])
amo_ev = [e for e in exA.events if e[0] == "place" and e[1] == "AMO"]
check("16:01: the target goes in as an AMO - SELL 65 LIMIT 210.00 DAY, for the next open",
      amo_ev == [("place", "AMO", "SELL", "DAY", "65", "210.00")], str(amo_ev))
check("...saved under the job id: job.amo, the target order (amo_target), the orders list; Telegram told",
      jA.get("amo", {}).get("order_id") and jA["target_order"]["kind"] == "amo_target"
      and jA["target_order"]["variety"] == "AMO" and "amo_target" in [o["kind"] for o in jA["orders"]]
      and any("AMO target" in x for x in sentA), str(jA.get("amo")))
at(2026, 10, 22, 16, 30)
sA.step()
check("...placed once", len([e for e in exA.events if e[0] == "place" and e[1] == "AMO"]) == 1)
exA.open_market(120.0)
at(2026, 10, 23, 9, 15, 5)
sA.step()
at(2026, 10, 23, 9, 16)
n_before = len([e for e in exA.events if e[0] == "place"])
sA.step()
jA = stA.get_job(jA["job_id"])
check("next day, opened +13%: hold - the AMO stays at the exchange, no second sell placed",
      jA["decision"]["scenario"] == "hold" and len([e for e in exA.events if e[0] == "place"]) == n_before
      and len(exA.resting_sells()) == 1)
at(2026, 10, 23, 15, 14, 5)
exA.px = 118.0
sA.step()
jA = stA.get_job(jA["job_id"])
check("15:14, not hit: the AMO cancelled AS AMO, then sold at market; CLOSED time_exit, nothing left",
      ("cancel", jA["amo"]["order_id"], "AMO") in exA.events and jA["status"] == "closed"
      and jA["result"]["exit_reason"] == "time_exit" and exA.net == 0 and not exA.resting_sells(),
      str([e for e in exA.events if e[0] == "cancel"]))

sB, stB, exB, lgB, _ = live_desk()
exB.close_day()
at(2026, 10, 22, 16, 1)
sB.step()
exB.open_market(232.0)                       # a gap up through the target at the open
at(2026, 10, 23, 9, 15, 3)
sB.step()
jB = stB.recent_jobs()[0]
check("a spike at the open: the AMO fills there (232) - CLOSED as a target hit before the decision",
      jB["status"] == "closed" and jB["result"]["exit_reason"] == "target" and jB["result"]["exit_price"] == 232.0
      and not jB.get("decision"), str(jB.get("result", {}).get("exit_price")))

sC, stC, exC, lgC, _ = live_desk()
exC.close_day()
at(2026, 10, 22, 16, 1)
sC.step()
exC.open_market(70.0)
at(2026, 10, 23, 9, 16)
sC.step()
jC = stC.recent_jobs()[0]
iC = next(i for i, e in enumerate(exC.events) if e[0] == "cancel")
iB = next(i for i, e in enumerate(exC.events) if e[0] == "place" and e[2] == "BUY" and i > iC - 1 and e[1] == "NORMAL"
          and i > 2)
rs = exC.resting_sells()
check("opened -34%: the AMO cancelled (as AMO) BEFORE the top-up buy; then one target for the whole position",
      exC.events[iC][2] == "AMO" and iC < iB and jC["decision"]["scenario"] == "topup_50"
      and len(rs) == 1 and rs[0]["qty"] == jC["position"]["qty"] and rs[0]["variety"] == "NORMAL"
      and rs[0]["price"] == jC["target_price"], str(exC.events[-4:]))

sD, stD, exD, lgD, sentD = live_desk()
exD.close_day()
at(2026, 10, 22, 16, 1)
sD.step()
exD.open_market(120.0, reject_amo=True)
at(2026, 10, 23, 9, 15, 5)
sD.step()
jD = stD.recent_jobs()[0]
rsD = exD.resting_sells()
check("the AMO rejected at the open: a normal target 210 placed at once, and said",
      len(rsD) == 1 and rsD[0]["variety"] == "NORMAL" and rsD[0]["price"] == 210.0
      and jD["target_order"]["kind"] == "target_day2" and any("rejected at the open" in x for x in sentD))
at(2026, 10, 23, 9, 16)
sD.step()
check("...and the decision keeps it (still one sell)", len(exD.resting_sells()) == 1
      and stD.recent_jobs()[0]["decision"]["scenario"] == "hold")

sE, stE, exE, lgE, sentE = live_desk()
exE.close_day()
exE.refuse_amo = 3
tries = []
for hh, mm in ((16, 1), (16, 3), (16, 6), (16, 12), (16, 30)):
    at(2026, 10, 22, hh, mm)
    sE.step()
    tries.append(len([e for e in exE.events if e[0] == "place" and e[1] == "AMO"]))
check("an AMO refused at 16:01 is retried every 5 min, 3 tries in all, then said",
      tries == [1, 1, 2, 3, 3] and any("no more tries" in x for x in sentE), str(tries))
exE.open_market(120.0)
at(2026, 10, 23, 9, 16)
sE.step()
rsE = exE.resting_sells()
check("...and the target goes in normally at the decision", len(rsE) == 1 and rsE[0]["variety"] == "NORMAL")

at(2026, 10, 22, 15, 16)
PRICE["ltp"] = 106.0
sP, stP, _, _, _ = desk()
sP.on_message(msg(3, SAMPLE, 15, 16))
sP.step()
at(2026, 10, 22, 16, 1)
sP.step()
check("paper places no AMO (live only)", stP.open_job() and not stP.open_job().get("amo"))

section("10. Owner's rules of 24 Oct 2026")
from btst.session import AngelSessions, Api as SessApi, auth_failed   # noqa: E402
from btst import holidays as H                                        # noqa: E402
from btst.strategy import BtstStrategy as _BS                         # noqa: E402

TCLK = {"t": 1000.0}


class FakeSmart:
    def __init__(self, n, bad_first=0, raise_first=0):
        self.n, self.bad, self.raise_ = n, bad_first, raise_first

    def ltpData(self, *a):
        if self.raise_:
            self.raise_ -= 1
            raise Exception("Invalid Token")
        if self.bad:
            self.bad -= 1
            return {"status": False, "errorcode": "AG8001", "message": "Invalid Token", "data": None}
        return {"status": True, "data": {"ltp": "101.0", "session": self.n}}


logins = {"n": 0, "fail": 0}


def login_fn():
    if logins["fail"]:
        logins["fail"] -= 1
        raise ConnectionError("login down")
    logins["n"] += 1
    return SimpleNamespace(api=FakeSmart(logins["n"]))


slog = []
at(2026, 10, 23, 8, 0)
ss = AngelSessions(login_fn, log=slog.append, now=lambda: CLOCK["t"], clock=lambda: TCLK["t"])
api_ = SessApi(ss)
api_.ltpData("NSE", "NIFTY", "1")
api_.ltpData("NSE", "NIFTY", "1")
check("one Angel login for the day, reused", logins["n"] == 1)
at(2026, 10, 23, 9, 13)
ss.daily("09:14")
n_before = logins["n"]
at(2026, 10, 23, 9, 14, 2)
TCLK["t"] += 4000
ss.daily("09:14")
at(2026, 10, 23, 9, 30)
ss.daily("09:14")
check("every trading day at 09:14: a fresh login before the open - once", n_before == 1 and logins["n"] == 2
      and any("daily refresh" in x for x in slog))
at(2026, 10, 24, 9, 20)
TCLK["t"] += 4000
ss.daily("09:14")
check("...not on a Saturday", logins["n"] == 2 and ss.refreshed_on.isoformat() == "2026-10-23")
api_.ltpData("NSE", "NIFTY", "1")
check("...where the day's first call logs in (a new day)", logins["n"] == 3)
TCLK["t"] += 4000
ss.api.bad = 1
r_ = api_.ltpData("NSE", "NIFTY", "1")
check("a call refused for its token (AG8001 Invalid Token): logs in again and is sent once more",
      r_["status"] is True and r_["data"]["session"] == 4 and ss.auth_refreshes == 1, str(r_))
TCLK["t"] += 4000
ss.api.raise_ = 1
r_ = api_.ltpData("NSE", "NIFTY", "1")
check("...the same for a call that raises 'Invalid Token'", r_["status"] is True and r_["data"]["session"] == 5
      and ss.auth_refreshes == 2, str(r_))
ss.api.bad = 5
r_ = api_.ltpData("NSE", "NIFTY", "1")
check("...but never a login storm: a token refused right after a login is not logged in again within 60 s",
      logins["n"] == 5 and r_["status"] is False)
check("auth errors told apart from others", auth_failed({"status": False, "errorcode": "AB1010"})
      and not auth_failed({"status": False, "errorcode": "AB2001", "message": "Order rejected"})
      and auth_failed(exc=Exception("Token Expired")) and not auth_failed(exc=Exception("timed out")))
at(2026, 10, 26, 9, 0)
TCLK["t"] += 4000
logins["fail"] = 5
try:
    ss.ensure()
except Exception:
    pass
calls_before = logins["fail"]
try:
    ss.ensure()
    blocked = False
except RuntimeError as e:
    blocked = "next try in" in str(e)
check("a failed login is not retried for 60 s (every call fails fast with the reason)",
      blocked and logins["fail"] == calls_before)
TCLK["t"] += 61
logins["fail"] = 0
check("...then tried again", ss.ensure() is not None and ss.failures == 1)

ans = {"status": "success", "data": [
    {"date": "2027-01-26", "holiday_type": "TRADING_HOLIDAY", "closed_exchanges": ["NSE", "NFO", "BSE", "BFO"]},
    {"date": "2027-03-22", "holiday_type": "TRADING_HOLIDAY", "closed_exchanges": ["MCX"]},
    {"date": "2027-04-01", "holiday_type": "SETTLEMENT_HOLIDAY", "closed_exchanges": ["NFO"]}]}
check("Upstox holidays: only trading holidays that close NFO/BFO", H.parse(ans) == ["2027-01-26"])
cache = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".holtest.json")
hl = []
Hd = H.Holidays(log=hl.append, fetch=lambda url: ans, cache=cache, now=lambda: CLOCK["t"])
Hd.refresh()
from arb.instruments import holidays as _hol, market_phase as _mp  # noqa: E402
check("...fetched and added to the desk's holidays (2027-01-26 is now a holiday)",
      "2027-01-26" in _hol() and _mp(datetime(2027, 1, 26, 10, 0, tzinfo=IST), "09:15", "15:45") == "holiday")
H2 = H.Holidays(log=hl.append, fetch=lambda url: (_ for _ in ()).throw(OSError("403")), cache=cache,
                now=lambda: CLOCK["t"])
H2.refresh()
check("...a failed fetch uses the cached answer", "cached" in H2.source and any("cached" in x for x in hl))
os.remove(cache)
check("the next trading day skips weekends and holidays (Fri 23 Oct -> Mon 26; Mon 9 Nov -> Wed 11, Diwali)",
      st._next_session(date(2026, 10, 23)) == date(2026, 10, 26)
      and st._next_session(date(2026, 11, 9)) == date(2026, 11, 11))

TIERS = {"topup_first_loss_pct": 25.0, "topup_first_pct": 50.0, "topup_second_loss_pct": 50.0,
         "target_cut_pct": 25.0, "floor_target_pct": 10.0, "trail_step_points": 1.0, "trail_modify_gap_sec": 0}


def paper_day(open_px, cfg=None):
    at(2026, 10, 22, 15, 16)
    PRICE["ltp"] = 106.0
    s_, st_, br_, lg_, sent_ = desk(cfg=dict(TIERS, **(cfg or {})))
    s_.on_message(msg(3, SAMPLE, 15, 16))
    s_.step()
    at(2026, 10, 23, 9, 16)
    PRICE["ltp"] = open_px
    s_.step()
    return s_, st_, br_, lg_


s60, st60, br60, lg60 = paper_day(79.0)            # -25.5% at the open
j60 = st60.open_job()
check("-25.5% at the open: +1 lot @ 79 (50% of Rs 6,890), average 92.50, target 157.50, trailing from 79, "
      "floor 92.50 + 10% = 101.75",
      j60["topped_pct"] == 50 and j60["position"]["qty"] == 130 and j60["position"]["avg_price"] == 92.5
      and j60["target_price"] == 157.5 and j60["trail"]["ref"] == 79.0 and j60["trail"]["floor"] == 101.75,
      str({k: j60.get(k) for k in ("target_price", "trail")})[:200])
oid60 = j60["target_order"]["order_id"]
at(2026, 10, 23, 9, 30)
PRICE["ltp"] = 76.6
s60.step()
j60 = st60.open_job()
check("2.4 points under the top-up buy: the target 2 points lower (155.50), the same order modified",
      j60["target_price"] == 155.5 and br60.orders[oid60]["price"] == 155.5 and len(j60["trail"]["steps"]) == 1
      and j60["target_history"][-1]["kind"] == "trail", str(j60["target_price"]))
at(2026, 10, 23, 9, 40)
PRICE["ltp"] = 85.0
s60.step()
check("...a bounce does not lift it back", st60.open_job()["target_price"] == 155.5)
at(2026, 10, 23, 9, 50)
PRICE["ltp"] = 60.0
s60.step()
check("...60 (-43%, not yet -50%): 19 points under 79 -> target 138.50", st60.open_job()["target_price"] == 138.5)
at(2026, 10, 23, 10, 0)
PRICE["ltp"] = 52.0
s60.step()
j60 = st60.open_job()
avg60 = round((6890 + 5135 + 3380) / 195, 2)
check(f"52 (-50.9%): the other 50% (65 @ 52) - average {avg60}, target = average + 10% = {tick_round(avg60 * 1.1)}, "
      f"trailing stopped",
      j60["topped_pct"] == 100 and j60["position"]["avg_price"] == avg60 and j60["target_price"] == tick_round(avg60 * 1.1)
      and not j60["trail"]["active"] and "top-up to 100%" in j60["trail"]["stop_reason"])
at(2026, 10, 23, 11, 0)
PRICE["ltp"] = 88.0
s60.step()
j60 = st60.recent_jobs()[0]
check("the price comes back over the target: CLOSED as a target hit, the trail and the top-ups saved",
      j60["status"] == "closed" and j60["result"]["exit_reason"] == "target" and j60["result"]["trailed"]
      and j60["result"]["trail_steps"] == 2 and j60["result"]["topups"] == 2, str(j60.get("result", {}).get("exit_price")))
check("every target change is in its history: entry, the day's, after each top-up, 2 trail steps",
      [h["kind"] for h in j60["target_history"]] == ["target", "target_day2", "target_after_topup_first", "trail",
                                                      "trail", "target_after_topup_second"],
      str([h["kind"] for h in j60["target_history"]]))
sF, stF, brF, _ = paper_day(79.0, {"topup_second_loss_pct": 95.0})
at(2026, 10, 23, 9, 30)
PRICE["ltp"] = 20.0
sF.step()
jF = stF.open_job()
check("trailing never goes under the new average + 10%: there it stops (101.75)",
      jF["target_price"] == 101.75 and not jF["trail"]["active"] and "floor" in jF["trail"]["stop_reason"],
      str(jF["target_price"]))
at(2026, 10, 23, 9, 40)
PRICE["ltp"] = 15.0
sF.step()
check("...and stays there", stF.open_job()["target_price"] == 101.75)

sLT, stLT, exLT, _, _ = live_desk(cfg=TIERS)
exLT.close_day()
at(2026, 10, 22, 16, 1)
sLT.step()
exLT.open_market(79.0)
at(2026, 10, 23, 9, 16)
sLT.step()
jLT = stLT.open_job()
n_place = len([e for e in exLT.events if e[0] == "place"])
at(2026, 10, 23, 9, 20)
exLT.px = 76.6
sLT.step()
jLT2 = stLT.open_job()
rsLT = exLT.resting_sells()
check("live: the trailing target is Angel's modifyOrder on the one resting sell - no second sell",
      jLT["decision"]["scenario"] == "topup_50" and len(rsLT) == 1 and rsLT[0]["price"] == jLT2["target_price"]
      and jLT2["target_price"] == round(jLT["target_price"] - 2, 2)
      and len([e for e in exLT.events if e[0] == "place"]) == n_place and any(e[0] == "modify" for e in exLT.events),
      f"{jLT['target_price']} -> {jLT2['target_price']}, resting {[r['price'] for r in rsLT]}")

sW, stW, exW, lgW, sentW = live_desk(day=23, cfg={"amo_at": "17:00"})     # a Friday
exW.close_day()
at(2026, 10, 23, 17, 0)
sW.step()
exW.drop_amos()                                    # Angel drops it on Saturday
at(2026, 10, 24, 17, 0, 5)
sW.step()
at(2026, 10, 25, 17, 0, 5)                         # Sunday: still there
sW.step()
jW = stW.open_job()
check("Friday entry: AMO at 17:00 Friday; dropped Saturday -> placed again Saturday 17:00; kept Sunday",
      [a["action"] for a in jW["amo_history"]] == ["placed", "placed", "kept"]
      and len([e for e in exW.events if e[0] == "place" and e[1] == "AMO"]) == 2
      and "cancelled" in jW["amo_history"][1]["why"], str([(a["slot"], a["action"]) for a in jW.get("amo_history", [])]))
at(2026, 10, 26, 8, 30)
sW.step()
check("...Monday 08:30: Sunday's check is done, nothing more", len(stW.open_job()["amo_history"]) == 3)
exW.open_market(120.0)
at(2026, 10, 26, 9, 16)
sW.step()
check("...Monday: the decision keeps the AMO (one sell at the exchange)",
      stW.open_job()["decision"]["scenario"] == "hold" and len(exW.resting_sells()) == 1)

at(2026, 10, 22, 15, 16)
PRICE["ltp"] = 106.0
sO, stO, brO, lgO, _ = desk()
sO.on_message(msg(3, SAMPLE, 15, 16))
sO.step()
at(2026, 10, 26, 9, 15, 10)                        # the desk was down all of Friday 23
PRICE["ltp"] = 99.0
sO.step()
jO = stO.recent_jobs()[0]
check("the desk down on the planned day (Fri): out at market at the next open, 'overdue_exit'",
      jO["status"] == "closed" and jO["result"]["exit_reason"] == "overdue_exit", str(jO.get("result", {}).get("exit_reason")))

at(2026, 10, 22, 15, 16)
PRICE["ltp"] = 106.0
sC1, stC1, brC1, lgC1, _ = desk()
sC1.on_message(msg(3, SAMPLE, 15, 16))
sC1.step()
at(2026, 10, 24, 11, 0)                            # Saturday: the market shut, the trade open
PRICE["ltp"] = 98.5
sC1.step()
jC1 = stC1.open_job()
rowC1 = [r for r in stats.by_day(stC1.recent_jobs()) if r["job_id"]][0]
check("market shut, a trade open: its last traded price fetched, P&L on it (98.50 vs 106: -7.08%, Rs -488)",
      jC1["last_ltp"] == 98.5 and "market closed" in jC1["last_ltp_source"] and rowC1["live_pct"] == -7.08
      and round(rowC1["live_pnl"]) == -488 and rowC1["now_source"] == jC1["last_ltp_source"], str(rowC1["live_pct"]))
PRICE["ltp"] = 97.0
sC1.step()
check("...not fetched again within 10 minutes", stC1.open_job()["last_ltp"] == 98.5)
sC2 = BtstStrategy(sC1.cfg, stC1, brC1, lambda: MASTER, None, "test_channel", log=lgC1.append, now=lambda: CLOCK["t"])
sC2.resume()
sC2.step()
check("...and fetched at once after a restart", stC1.open_job()["last_ltp"] == 97.0)

SZ = {"trade_amount": 15000, "amount_tolerance_pct": 5}
at(2026, 10, 22, 15, 16)
PRICE["ltp"] = 106.0
sS1, stS1, _, _, _ = desk(cfg=SZ)
sS1.on_message(msg(3, SAMPLE, 15, 16))
sS1.step()
jS1 = stS1.open_job()
check("Rs 15,000 +/- 5%: NIFTY @ 106, lot 65 (Rs 6,890) -> 2 lots = 130 qty, Rs 13,780 (never above Rs 15,750)",
      jS1 and jS1["lots"] == 2 and jS1["position"]["qty"] == 130 and "always below" in jS1["sizing"], str(jS1 and jS1.get("sizing")))
PRICE["ltp"] = 250.0
sS3, stS3, brS3, _, sentS3 = desk(cfg=SZ)
sS3.on_message(msg(3, SAMPLE, 15, 16))
sS3.step()
jS3 = stS3.recent_jobs()[0]
check("...one lot costing more than Rs 15,750 (65 x 250 = Rs 16,250): not traded, said, nothing bought",
      jS3["status"] == "rejected" and "not below" in jS3["reject_reason"] and brS3.n == 0
      and any("not below" in x for x in sentS3), jS3.get("reject_reason", ""))
check("...and the amount is the same every trade - no compounding (the config's Rs 15,000, not the P&L)",
      sS1._size({"lot_size": 65}, 106.0)[0] == 2 and sS1._size({"lot_size": 20}, 475.0)[0] == 1
      and sS1._size({"lot_size": 65}, 82.0)[0] == 2 and sS1._size({"lot_size": 65}, 115.0)[0] == 2)
check("the total stays strictly below Rs 15,750: 2 lots of exactly Rs 7,875 (= 15,750) -> 1 lot; Rs 7,874.95 -> 2",
      sS1._size({"lot_size": 25}, 315.0)[0] == 1 and sS1._size({"lot_size": 25}, 314.998)[0] == 2)
PRICE["ltp"] = 106.0

hb = Logbook(echo=False)
hb.hide("https://t.me/some_chan", "some_chan")
hb.log("INFO  channel | starting the reader on https://t.me/some_chan")
hb.log("[some_chan #12 23 Oct 15:36:13] #BTST TRADE")
hrows, _ = hb.lines()
check("the channel's link and name never reach the log view: replaced by 'channel'",
      all("some_chan" not in r["text"] for r in hrows) and any("[channel #12" in r["text"] for r in hrows),
      str([r["text"] for r in hrows]))
fd2, path2 = tempfile.mkstemp(suffix=".log")
with os.fdopen(fd2, "w") as fh:
    fh.write("[2026-10-23 15:38:51] INFO  channel | [some_chan #22932 23 Oct 15:36:13] old line\n")
hb2 = Logbook(path=path2, echo=False)
hb2.hide("some_chan")
check("...lines already in btst.log too", "some_chan" not in hb2.lines()[0][0]["text"])
os.remove(path2)
sst = web.state(st, store)
check("the page's state carries no channel name (no 'channel' key, none in messages or jobs)",
      "test_channel" not in _json.dumps(sst, default=str) and "channel" not in sst)

section("11. Switching paper <-> live")
from btst import modes                                           # noqa: E402

at(2026, 10, 22, 15, 16)
PRICE["ltp"] = 106.0
sM, stM, brM, lgM, sentM = desk()
sM.on_message(msg(3, SAMPLE, 15, 16))
sM.step()
paper_jid = stM.open_job()["job_id"]
exM = Exchange()
exM.px = 106.0
saved = []


def mk(to):
    return (AngelBtstBroker(exM, sleep=lambda s: None, log=lambda *_: None) if to == "live"
            else PaperBtstBroker(lambda c: PRICE["ltp"], log=lambda *_: None, today=lambda: CLOCK["t"].date()))


at(2026, 10, 22, 15, 20)
mlog = []
rM = modes.switch(sM, "live", make_broker=mk, save_mode=saved.append, notify=sentM.append, log=mlog.append)
jP = stM.get_job(paper_jid)
check("PAPER -> LIVE with a paper trade open: switched at once; the paper trade parked with its state saved",
      rM["ok"] and sM.broker.mode == "live" and saved == ["live"] and jP["status"] == "parked"
      and jP["parked"]["qty"] == 65 and jP["parked"]["avg_price"] == 106.0 and not stM.open_job()
      and "parked" in rM["note"], str(rM))
sM.cfg["markers"] = ["BTST", "Disclaimer"]
sM.on_message(msg(9, SAMPLE.replace("22800", "22850").replace("Call", "Call"), 15, 21))
MASTER_EXTRA = dict(MASTER[1], symbol="NIFTY27OCT2622850CE", token="9", strike="2285000.000000")
sM.master_fn = lambda: MASTER + [MASTER_EXTRA]
sM.llm = None
sM.step()
jL = stM.open_job()
check("...the parked paper trade does not block a live signal the same day: bought LIVE",
      jL and jL["mode"] == "live" and jL["contract"]["symbol"] == "NIFTY27OCT2622850CE" and exM.net > 0,
      str(stM.recent_jobs()[0].get("reject_reason") if not jL else jL["contract"]["symbol"]))
rM2 = modes.switch(sM, "paper", make_broker=mk, save_mode=saved.append, log=mlog.append)
check("LIVE -> PAPER with a live trade open, without the exit choice: refused, nothing changes",
      not rM2["ok"] and rM2.get("need_exit") and sM.broker.mode == "live" and stM.open_job()["job_id"] == jL["job_id"])
at(2026, 10, 22, 16, 30)
rM3 = modes.switch(sM, "paper", exit_trade=True, make_broker=mk, save_mode=saved.append, log=mlog.append)
check("...with the exit choice but the market shut: refused, still LIVE, the trade still open",
      not rM3["ok"] and "market is closed" in rM3["error"] and sM.broker.mode == "live" and stM.open_job())
at(2026, 10, 22, 15, 25)
exM.px = 112.0
rM4 = modes.switch(sM, "paper", exit_trade=True, make_broker=mk, save_mode=saved.append, log=mlog.append)
jL2 = stM.get_job(jL["job_id"])
jP2 = stM.get_job(paper_jid)
check("...with the exit choice in market hours: sold at market (closed 'mode_switch_exit'), then PAPER, "
      "and the parked paper trade resumed and taken back by the paper broker",
      rM4["ok"] and jL2["status"] == "closed" and jL2["result"]["exit_reason"] == "mode_switch_exit" and exM.net == 0
      and sM.broker.mode == "paper" and jP2["status"] == "open" and sM.broker.net_qty(jP2["contract"]) == 65
      and "resumed" in rM4["note"], str(rM4))
rM5 = modes.switch(sM, "paper", make_broker=mk, save_mode=saved.append, log=mlog.append)
check("switching to the mode it is already in changes nothing", rM5["ok"] and "already" in rM5["note"])
at(2026, 10, 22, 15, 26)
sQ, stQ, _, _, _ = desk()
rQ = modes.switch(sQ, "live", make_broker=mk, save_mode=saved.append, log=mlog.append)
rQ2 = modes.switch(sQ, "paper", make_broker=mk, save_mode=saved.append, log=mlog.append)
check("no trade open: a simple switch both ways", rQ["ok"] and rQ2["ok"] and sQ.broker.mode == "paper"
      and saved[-2:] == ["live", "paper"])
stateM = web.state(sM, stM)
check("the page's state lists parked trades", "parked" in stateM and isinstance(stateM["parked"], list))

fd3, path3 = tempfile.mkstemp(suffix=".log")
with os.fdopen(fd3, "w") as fh:
    fh.write("[2026-10-24 17:25:18] INFO  heartbeat | alive\n"
             "Exception occurred during processing of request from ('167.94.146.62', 51160)\n"
             "Traceback (most recent call last):\n  File \"/usr/lib/python3.12/socket.py\", line 707, in readinto\n"
             "ConnectionResetError: [Errno 104] Connection reset by peer\n")
wrows = Logbook(path=path3, echo=False).lines(group="errors")[0]
check("a scanner hanging up on the page (old tracebacks in btst.log) is not shown as an error",
      not wrows, str(wrows)[:200])
os.remove(path3)
wlog = []
srv = web._Server(("127.0.0.1", 0), web.make_handler(st, store, lambda to, exit_trade=False: {}, None))
srv.book = SimpleNamespace(log=wlog.append)
try:
    raise ConnectionResetError(104, "Connection reset by peer")
except ConnectionResetError:
    srv.handle_error(None, ("1.2.3.4", 1))
try:
    raise ValueError("bad request line")
except ValueError:
    srv.handle_error(None, ("1.2.3.4", 2))
srv.server_close()
check("...the page server ignores a client hang-up, and logs any other request error as one line",
      len(wlog) == 1 and "bad request line" in wlog[0] and "web |" in wlog[0], str(wlog))

section("12. GIFT Nifty (information only)")
from btst import gift as G                                       # noqa: E402

check("GIFT direction: a fall is against a CE, a rise against a PE",
      G.against("CE", -80) == "against" and G.against("PE", 80) == "against" and G.against("CE", 60) == "with"
      and G.against("PE", -60) == "with" and G.against("CE", 0.4) == "flat")
GQ = {"price": 22683.0}
gcalls = {"n": 0, "fail": 0}


def fake_gift(today):
    gcalls["n"] += 1
    if gcalls["fail"]:
        gcalls["fail"] -= 1
        raise OSError("nseix down")
    return {"price": GQ["price"], "prev_close": 22565.0, "day_change": GQ["price"] - 22565.0, "underlying": 22600.0,
            "expiry": "27-Oct-2026", "high": 22795.5, "low": 22451.5}


at(2026, 10, 23, 15, 16)                            # a Friday CE trade
PRICE["ltp"] = 106.0
sG, stG, brG, lgG, sentG = desk()
sG.gift_fetch = fake_gift
sG.on_message(msg(3, SAMPLE, 15, 16, day=23))
sG.step()
at(2026, 10, 23, 15, 50)
sG.step()
jG = stG.open_job()
check("the entry day's close: GIFT Nifty's baseline saved (22,683)", jG["gift_close"]["price"] == 22683.0)
at(2026, 10, 24, 1, 0)                              # Saturday 01:00: not the next session's date
sG.step()
check("...no check on a night that is not before the next session (Saturday)", not stG.open_job().get("gift_check"))
at(2026, 10, 26, 1, 0, 5)                           # Monday 01:00
GQ["price"] = 22603.0
gcalls["fail"] = 1
sG.step()
check("Monday 01:00: a failed read is logged and does not stop anything", not stG.open_job().get("gift_check")
      and any("GIFT Nifty check not read" in x for x in lgG))
sG.gift_failed_at -= 400
at(2026, 10, 26, 1, 6)
sG.step()
jG = stG.open_job()
check("...read again 5 min later: -80 points since the close - AGAINST our CE, said on Telegram, saved",
      jG["gift_check"]["move"] == -80.0 and jG["gift_check"]["verdict"] == "against"
      and any("AGAINST our CE" in x for x in sentG), str(jG.get("gift_check")))
n_calls = gcalls["n"]
at(2026, 10, 26, 2, 0)
sG.step()
check("...once a night", gcalls["n"] == n_calls)
check("the page's next session is the trade's (Mon 26 at 02:00 for a Friday trade), not the day after today",
      web.state(sG, stG)["next_session"] == "2026-10-26")
check("...information only: the trade, its target and its orders are untouched",
      jG["status"] == "open" and jG["target_price"] == 210.0 and not jG.get("topups") and not jG.get("decision"))

section("13. Recording a trade's prices, and its chart")
import shutil                                                    # noqa: E402
from btst import recorder as REC                                 # noqa: E402

tmpdir = tempfile.mkdtemp(prefix="btst_rec_")
rc = REC.TickRecorder(folder=tmpdir)
base_s = datetime(2026, 10, 23, 10, 0, tzinfo=IST).timestamp()
wrote = [rc.record("J1", base_s, 100.0), rc.record("J1", base_s + 5, 100.0), rc.record("J1", base_s + 10, 101.5),
         rc.record("J1", base_s + 45, 101.5), rc.record("J1", base_s + 70, 99.0)]
tk = rc.read("J1")
check("the local file: a line when the price changes, or after 30 s unchanged", wrote == [True, False, True, True, True]
      and [p_ for _, p_ in tk] == [100.0, 101.5, 101.5, 99.0], str(wrote))
bb = REC.bars(tk)
check("1-minute bars from the ticks: open/high/low/close per minute",
      len(bb) == 2 and bb[0]["o"] == 100.0 and bb[0]["h"] == 101.5 and bb[0]["c"] == 101.5 and bb[1]["c"] == 99.0)
mg = REC.merge(bb, [{"t": bb[0]["t"], "o": 1, "h": 1, "l": 1, "c": 1}, {"t": bb[1]["t"] + 60000, "o": 98, "h": 98.5, "l": 97, "c": 98}])
check("...a minute with no ticks is filled from Angel's candle, a minute with ticks keeps them",
      len(mg) == 3 and mg[0]["c"] == 101.5 and mg[2]["src"] == "candle")
check("the gzip round trip gives the ticks back", REC.unzip(rc.compress("J1")) == tk)

rw = PriceWatch(rest_quote=None, log=lambda *_: None, use_ws=False, today=lambda: CLOCK["t"].date())
seen_ticks = []
rw.on_tick = lambda tok, px, ts: seen_ticks.append((tok, px))
rw.set_extra([{"token": "77", "symbol": "X77", "exchange": "NFO"}])
rw.see("77", 12.5, 13.0, "websocket")
rw.see("88", 9.0, 9.0, "websocket")
check("the socket's ticks for a recorded contract go to the recorder (others do not)", seen_ticks == [("77", 12.5)])


def rec_desk():
    at(2026, 10, 22, 15, 16)
    PRICE["ltp"] = 106.0
    s_, st_, br_, lg_, sent_ = desk()
    s_.recorder = REC.TickRecorder(folder=tmpdir)
    s_.candles_fn = lambda c, a, b: [{"t": int(datetime(2026, 10, 23, 14, 0, tzinfo=IST).timestamp() * 1000),
                                      "o": 150.0, "h": 151.0, "l": 149.0, "c": 150.5, "n": 0}] if a.date() == date(2026, 10, 23) else []
    s_.on_message(msg(3, SAMPLE, 15, 16))
    s_.step()
    return s_, st_, br_, lg_


sR, stR, brR, lgR = rec_desk()
jR = stR.open_job()
check("the buy starts the recording: until 15:45 of the next trading day", jR["recording"]["status"] == "recording"
      and jR["recording"]["until"] == "2026-10-23T15:45:00+05:30", str(jR.get("recording")))
tokR = jR["contract"]["token"]


def tick_at(h, m, sec, px):
    at(2026, 10, 23, h, m, sec)
    sR._on_tick(tokR, px, CLOCK["t"].timestamp())


sR.rec_checked = 0
sR.step()
check("...the contract is followed for it", tokR in sR.rec_map)
at(2026, 10, 22, 15, 20)
sR._on_tick(tokR, 107.0, CLOCK["t"].timestamp())
tick_at(9, 15, 5, 120.0)
at(2026, 10, 23, 9, 15, 6)
PRICE["ltp"] = 120.0
sR.step()
tick_at(10, 30, 0, 190.0)
tick_at(11, 0, 0, 211.0)
at(2026, 10, 23, 11, 0, 1)
PRICE["ltp"] = 211.0
sR.step()
check("the target hit at 11:00 closes the trade...", stR.get_job(jR["job_id"])["status"] == "closed")
sR.rec_checked = 0
at(2026, 10, 23, 13, 0)
sR.step()
tick_at(13, 0, 0, 230.0)
tick_at(15, 30, 0, 180.0)
check("...but the recording goes on past the exit (until 15:45)", stR.get_job(jR["job_id"])["recording"]["status"] == "recording"
      and tokR in sR.rec_map and len(sR.recorder.read(jR["job_id"])) == 6, str(len(sR.recorder.read(jR["job_id"]))))
live_chart = sR.chart(jR["job_id"])
check("while recording, the chart comes from the local file", live_chart["source"] == "recording" and len(live_chart["bars"]) == 6)
at(2026, 10, 23, 15, 48)
sR.rec_checked = 0
sR.step()
jR = stR.get_job(jR["job_id"])
tdoc = stR.get_ticks(jR["job_id"], raw=True)
check("after 15:45: saved to MongoDB - 6 prices, 7 one-minute bars (one gap from Angel's candles), gzipped, "
      "with the events; the local file removed",
      jR["recording"]["status"] == "saved" and tdoc["count"] == 6 and len(tdoc["bars"]) == 7
      and tdoc["candle_minutes"] == 1 and REC.unzip(tdoc["gz"])[-1][1] == 180.0
      and [e["kind"] for e in tdoc["events"]][0] == "buy" and tdoc["events"][-1]["kind"] == "exit"
      and not os.path.exists(sR.recorder.path(jR["job_id"])), str({k: tdoc.get(k) for k in ("count", "candle_minutes")}))
ch = sR.chart(jR["job_id"])
b_open = [b for b in ch["bars"] if b["open"]]
b_after = [b for b in ch["bars"] if not b["open"]]
check("the chart from MongoDB: P&L (price - average) x qty while open, the realised P&L after the exit, "
      "'if held' still moving",
      ch["source"] == "mongo" and b_open and b_open[-1]["pnl"] == round((211.0 - 106.0) * 65, 2)
      and b_after and b_after[-1]["pnl"] == ch["exit"]["net_pnl"] and b_after[-1]["hold_pnl"] == round((180 - 106) * 65, 2)
      and b_open[0]["target"] == 210.0, str([(b["c"], b["pnl"], b["hold_pnl"]) for b in ch["bars"]])[:300])
check("...and the events for the markers: buy, the targets, the exit",
      [e["kind"] for e in ch["events"]][:1] == ["buy"] and ch["events"][-1]["kind"] == "exit"
      and any(e["kind"] == "target" for e in ch["events"]))
at(2026, 10, 22, 15, 16)
PRICE["ltp"] = 106.0
sN, stN, brN, lgN, _ = desk()                       # a desk without a recorder: the trade is not recorded
sN.on_message(msg(3, SAMPLE, 15, 16))
sN.step()
no_rec = not stN.open_job().get("recording")
sN2 = BtstStrategy(sN.cfg, stN, brN, lambda: MASTER, None, "test_channel", log=lgN.append, now=lambda: CLOCK["t"],
                   recorder=REC.TickRecorder(folder=tmpdir))
sN2.resume()
check("a restart with an open trade that was never recorded starts its recording (to the next session's 15:45)",
      no_rec and stN.open_job()["recording"]["status"] == "recording"
      and stN.open_job()["recording"]["until"] == "2026-10-23T15:45:00+05:30")
at(2026, 10, 22, 15, 16)
PRICE["ltp"] = 106.0
sK, stK, _, lgK, _ = desk()
sK.recorder = REC.TickRecorder(folder=tmpdir)
kcalls = {"n": 0}


def flaky_candles(c, a, b):
    kcalls["n"] += 1
    raise RuntimeError("Access denied because of exceeding access rate")


sK.candles_fn = flaky_candles
sK.on_message(msg(3, SAMPLE, 15, 16))
sK.step()
jK = stK.open_job()
sK._on_tick(jK["contract"]["token"], 106.5, CLOCK["t"].timestamp())
at(2026, 10, 23, 15, 48)
sK.rec_checked = 0
sK.step()
first = stK.get_job(jK["job_id"])["recording"]["status"]
for k in range(2):
    sK.rec_failed[jK["job_id"]] = 0.0
    sK.rec_checked = 0
    sK.step()
check("Angel's candles rate-limited: the save waits and tries again (the local file kept), then saves the ticks alone",
      first == "recording" and stK.get_job(jK["job_id"])["recording"]["status"] == "saved"
      and stK.get_ticks(jK["job_id"])["count"] == 1, f"{first} -> {stK.get_job(jK['job_id'])['recording']['status']}")
shutil.rmtree(tmpdir, ignore_errors=True)

print(f"\n{'=' * 64}\n  {PASS} passed, {FAIL} failed\n{'=' * 64}")
sys.exit(1 if FAIL else 0)
