#!/usr/bin/env python3
"""End-to-end check of the LIVE shadow wiring (IntradayEngine._shadow_noise)
on recorded data, against the backtest generator.

For the last N sessions of .cache/history, minute by minute, the engine is
handed what the live candle store holds - the previous 5 sessions as the
history API returns them (the store's morning refresh writes exactly those
rows: .cache/candles/<index>_1m.json holds each earlier session from 09:15 to
its last bar) plus today's closed bars - with the clock 2 s after each bar's
close, a fake sigma fetch (history before the day) and no option chain
(index-only shadow: no contract, so no bid). `--cut1514` replays the other
shape instead: earlier sessions cut at 15:14 (a store whose refresh failed,
holding only the bars it built itself up to the square-off).
Its journal is then compared with research_out/momentum/screen.py:gen
(vm 1.0, minr 1.5, 09:45-14:45, noise trail, 14-session sigma):
  entries  decision bar, side, index stop
  gates    the journalled 4h trend and pace against the research's
           (common.py Features.h4_ret(t, 5), flow_noise_robust.atr_ratio_series)
  exits    the bar of a stop before the 15:14 square-off; gen's later stops
           and its 15:25 eod are the live square-off by design.
No network, no orders: fakes only.

    nice venv/bin/python research/noise_shadow_replay.py [sessions] [--cut1514]
"""
import json, os, sys, tempfile, threading, time
from datetime import datetime
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path[:0] = [os.path.join(ROOT, "research_out", "momentum"), os.path.join(ROOT, "research_out", "no_retest"),
                os.path.join(ROOT, "research", "lab"), ROOT, os.path.join(ROOT, "tools")]
import screen as S                                         # noqa: E402
import realistic as rz                                     # noqa: E402
from common import Features                                # noqa: E402
from flow_noise_robust import atr_ratio_series             # noqa: E402
import arb.intraday_engine as ie                           # noqa: E402
import arb.positions as pm                                 # noqa: E402
import arb.regime as rg                                    # noqa: E402
from arb import noise_breakout as nb                       # noqa: E402
from arb.config import CostModel, IntradayLimits           # noqa: E402
from arb.execution import PaperBroker                      # noqa: E402

INDICES = ["NIFTY", "BANKNIFTY", "SENSEX"]
LOG = []


class _Store:
    def __init__(self):
        self.ltp, self.bid, self.slot_of_token = [0.0], [0.0], {}


class _Feed:
    def __init__(self):
        self.store, self.spot_slots, self.tick_event = _Store(), {}, threading.Event()


class _Candles:
    interval, api, live = "1m", None, {}

    def __init__(self):
        self.rows, self.underlyings = {}, {}

    def get(self, name, limit=120):
        return self.rows.get(name, [])[-limit:]


class _J:
    def __init__(self, d):
        self.dir = d

    def record_trade(self, r):
        pass


class _Cfg:
    session_start, session_end = "09:15", "15:15"


class _Cap:
    capital = 13466.9

    def daily_target(self, pct):
        return self.capital * pct / 100.0


class _NoOrders(PaperBroker):
    def place_structure(self, *a, **k):
        raise AssertionError("order sent")


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    cut = "--cut1514" in sys.argv
    n_days = int(args[0]) if args else 20
    IX = {n: rz.Index(n) for n in INDICES}
    F = Features(IX)
    print(f"store shape: earlier sessions {'CUT AT 15:14' if cut else 'as the history API returns them'}")
    var = dict(name="N1", kind="noise", lo="09:45", hi="14:45", grid=30, vm=1.0, minr=1.5, exit=dict(trail="noise"))
    real = (time.time, ie.now_ist, pm.now_ist, rg.now_ist)
    tot = dict(ent=0, ent_ok=0, only_gen=0, only_live=0, stop_cmp=0, stop_ok=0, late=0, h4_ok=0, pace_ok=0,
               gaps=0)
    try:
        for name in INDICES:
            rows = IX[name].rows
            days = nb.by_day(rows); keys = sorted(days)
            P = S.prep(IX[name]); ref = S.gen(name, IX[name], P, var); AR = atr_ratio_series(P)
            jd = tempfile.mkdtemp(prefix="nb_replay_")
            L = IntradayLimits.load()
            L.nb_indices = (name,)
            cand = _Candles(); feed = _Feed(); feed.spot_slots = {name: 0}
            e = ie.IntradayEngine(feed, [], _Cfg(), CostModel(), L, _NoOrders(CostModel()), _J(jd), _Cap(),
                                  cand, log=LOG.append)
            hist = e._nb_history()
            hist.cache_dir = tempfile.mkdtemp(prefix="nb_replay_c_")
            hist.underlyings = {name: type("U", (), {"spot_exchange": "X", "spot_token": "0"})()}
            hist.fetch = lambda api, p, rows=rows: {"status": True, "data": [
                r for r in rows if r[0][:10] < p["todate"][:10] or r[0][:10] == p["todate"][:10]]}
            for d in keys[-n_days:]:
                k = keys.index(d)
                prev5 = [r for p in keys[max(0, k - 5):k] for r in days[p] if not cut or r[0][11:16] <= "15:14"]
                today = days[d]
                for m, bar in enumerate(today + [None]):
                    if bar is None:
                        break
                    close_ts = datetime.fromisoformat(bar[0]).timestamp() + 60.0
                    for off in (2.0, 30.0):
                        dt = datetime.fromtimestamp(close_ts + off, ie.now_ist().tzinfo)
                        time.time = lambda dt=dt: dt.timestamp()
                        ie.now_ist = pm.now_ist = rg.now_ist = lambda dt=dt: dt
                        cand.rows[name] = prev5 + today[:m + 1]
                        feed.store.ltp = [today[m][4]]
                        e._shadow_noise()
                        if hist._thread is not None:
                            hist._thread.join(10)
            recs = []
            for d in keys[-n_days:]:
                f = os.path.join(jd, f"shadow_{d}.jsonl")
                if os.path.exists(f):
                    recs += [json.loads(x) for x in open(f)]
            opens = {r["id"]: r for r in recs if r["event"] == "open"}
            exits = {r["id"]: r for r in recs if r["event"] == "exit"}
            days_set = set(keys[-n_days:])
            R = {rows[t["i"]][0]: t for t in ref if t["day"] in days_set}
            Lv = {r["decision_bar"]: r for r in opens.values()}
            both = sorted(set(R) & set(Lv))
            print(f"{name:10s} last {n_days} sessions: gen {len(R)} entries, live shadow {len(Lv)}, common {len(both)}, "
                  f"only gen {sorted(set(R) - set(Lv))}, only live {sorted(set(Lv) - set(R))}")
            tot["ent"] += len(both); tot["only_gen"] += len(set(R) - set(Lv)); tot["only_live"] += len(set(Lv) - set(R))
            for iso in both:
                t, o = R[iso], Lv[iso]
                r_stop = rows[t["i"]][4] - t["side"] * t["risk"]
                ok = t["side"] == o["side"] and abs(r_stop - o["index_stop0"]) < 1e-6
                tot["ent_ok"] += ok
                h_ref = F.h4_ret(dict(u=name, i=t["i"]), 5)
                p_ref = float(AR[t["i"]])
                h_ok = o["gates"]["trend_4h"] == h_ref
                p_ok = abs(o["gates"]["pace"] - round(p_ref, 4)) <= 1e-9
                tot["h4_ok"] += h_ok; tot["pace_ok"] += p_ok
                x = exits.get(o["id"])
                g_bar = rows[t["j"]][0]
                note = ""
                if t["why"] == "stop" and g_bar[11:16] < "15:14":
                    tot["stop_cmp"] += 1
                    good = x is not None and x["why"].startswith("index_stop") and x["exit_bar"] == g_bar
                    tot["stop_ok"] += good
                    note = "stop ok" if good else f"STOP MISMATCH live {x and (x['why'], x['exit_bar'])}"
                else:
                    tot["late"] += 1
                    note = f"gen {t['why']} {g_bar[11:16]} -> live {x and x['why']} {x and x['exit_bar'][11:16]}"
                if x is not None and x.get("gaps"):
                    tot["gaps"] += 1
                    note += f" | gaps {len(x['gaps'])}"
                print(f"   {iso[:16]} side {t['side']:+d} stop gen {r_stop:.2f} live {o['index_stop0']:.2f} "
                      f"{'ok' if ok else 'ENTRY MISMATCH'} | 4h {o['gates']['trend_4h']:+d}/{h_ref:+d} "
                      f"{'ok' if h_ok else 'GATE MISMATCH'} | pace {o['gates']['pace']:.4f}/{p_ref:.4f} "
                      f"{'ok' if p_ok else 'GATE MISMATCH'} | pc {o['pc_source']} | {note}")
    finally:
        time.time, ie.now_ist, pm.now_ist, rg.now_ist = real
    print(f"\nALL: common entries {tot['ent']} (side+stop match {tot['ent_ok']}), only gen {tot['only_gen']}, "
          f"only live {tot['only_live']}; gates 4h {tot['h4_ok']}/{tot['ent']} pace {tot['pace_ok']}/{tot['ent']}; "
          f"stops before 15:14 {tot['stop_ok']}/{tot['stop_cmp']} on the same bar; "
          f"{tot['late']} gen exits at/after 15:14 (live square-off); {tot['gaps']} trades with gaps")


if __name__ == "__main__":
    main()
