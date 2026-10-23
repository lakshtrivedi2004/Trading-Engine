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
def _match(doc, flt):
    for k, v in flt.items():
        val = doc.get(k)
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
            "markers": ["BTST", "Disclaimer"], "ai_fallback_regex": True, "ltp_poll_sec": 0}
    conf.update(cfg or {})
    logs, sent = [], []
    st = BtstStrategy(conf, store, broker, lambda: MASTER, model or Model(GOOD), "btst_karo",
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
r = S.parse(SAMPLE, llm=Model(dict(GOOD, strike_price=22900)))
check("a strike the message does not contain is refused", not r["is_signal"] and "22900" in r["reason"], r["reason"])
r = S.parse(SAMPLE, llm=Model(dict(GOOD, price=130)))
check("a target taken for the buy price (130 is in the text) is refused by the strict read",
      not r["is_signal"] and "disagree" in r["reason"], r["reason"][:100])
r = S.parse(SAMPLE, llm=Model(dict(GOOD, is_btst_signal=False, reason="a result post")))
check("the model saying 'not a signal' is no signal", not r["is_signal"] and "not a signal" in r["reason"])
r = S.parse(SAMPLE, llm=Model(boom=RuntimeError("429 quota")))
check("the model down: the strict read stands in", r["is_signal"] and r["parsed_by"] == "regex", r["reason"][:80])
r = S.parse(SAMPLE, llm=Model(boom=RuntimeError("429 quota")), ai_fallback_regex=False)
check("...unless ai_fallback_regex is off", not r["is_signal"])
put = SAMPLE.replace("NIFTY 22800 Call Buy 105", "SENSEX 73000 PUT buy 210")
check("PUT on SENSEX reads too", S.strict_read(put) ==
      {"indices": "SENSEX", "strike_price": 73000.0, "side": "PE", "price": 210.0})

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
      not store.message_seen("btst_karo", 1) and store.message_seen("btst_karo", 2) and not store.open_job())
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
check("a message at 15:31 is not read", not store.message_seen("btst_karo", 5))
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
check("opened -10%: within 20%, target stays 210", j["decision"]["scenario"] == "hold_small_loss"
      and j["target_price"] == 210.0, str(j.get("decision")))
at(2026, 10, 23, 15, 14, 2)
PRICE["ltp"] = 95.0
st.step()
done = store.get_job(j["job_id"])
r = done.get("result") or {}
check("15:14: target cancelled, sold at market (95), CLOSED time_exit with a loss",
      done["status"] == "closed" and r["exit_reason"] == "time_exit" and r["exit_price"] == 95.0
      and r["net_pnl"] < 0 and [o["kind"] for o in done["orders"]][-2:] == ["target_cancel", "exit"], str(r)[:120])

section("4c. Next day, down 33%: about the same amount again, target average + 10%")
at(2026, 10, 22, 15, 18)
PRICE["ltp"] = 105.0
st, store, broker, logs, sent = desk()
st.on_message(msg(20, SAMPLE, 15, 18))
st.step()
at(2026, 10, 23, 9, 15, 40)
PRICE["ltp"] = 70.0
st.step()
j = store.open_job()
d = j["decision"]
avg = (105 * 65 + 70 * 130) / 195
check("opened -33%: Rs 6,825 invested, Rs 4,550 a lot now -> 2 lots (Rs 9,100, the nearest whole lots)",
      d["scenario"] == "average_down" and d["added_qty"] == 130 and d["added_price"] == 70.0, str(d))
check("...average %.2f, target average + 10%% = %.2f on the tick" % (avg, tick_round(avg * 1.1)),
      abs(j["position"]["avg_price"] - round(avg, 2)) < 1e-6 and j["position"]["qty"] == 195
      and j["target_price"] == tick_round(avg * 1.1) and j["orders"][-1]["kind"] == "target_after_average",
      f"avg {j['position']['avg_price']}, target {j['target_price']}")
at(2026, 10, 23, 13, 0)
PRICE["ltp"] = 92.0
st.step()
done = store.get_job(j["job_id"])
r = done.get("result") or {}
check("the new target fills: CLOSED in profit on the averaged position",
      done["status"] == "closed" and r["target_hit"] and r["averaged"] and r["qty"] == 195 and r["net_pnl"] > 0,
      str(r)[:140])

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
stq.on_message(msg(40, SAMPLE, 15, 18))
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
book.log("[btst_karo channel #9 2026-10-22 15:17] line one\nline two failed")      # the reader's plain line
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

httpd = web.serve(st, store, lambda to: {"ok": False, "error": "test"}, port=0, host="127.0.0.1", book=book)
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
w4.last["at"] -= 10                          # the socket went quiet
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
    s_ = BtstStrategy(conf, store, br, lambda: MASTER, Model(GOOD), "btst_karo", log=lg.append,
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
check("yesterday's DAY target died at the close (as at the exchange); nothing filled",
      st9.get_job(j9["job_id"])["target_last_status"] == "cancelled" and st9.open_job())
at(2026, 10, 23, 9, 16)
s9.step()                                    # the decision: hold, the target 210 placed again for today
# a restart: a new paper broker and price watch, the same MongoDB
pw9c = watch()
s9b = BtstStrategy(s9.cfg, st9, PaperBtstBroker(pw9c.price, log=lambda *_: None, watch=pw9c,
                                                today=lambda: CLOCK["t"].date()),
                   lambda: MASTER, None, "btst_karo", log=lg9.append, now=lambda: CLOCK["t"], prices=pw9c)
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

print(f"\n{'=' * 64}\n  {PASS} passed, {FAIL} failed\n{'=' * 64}")
sys.exit(1 if FAIL else 0)
