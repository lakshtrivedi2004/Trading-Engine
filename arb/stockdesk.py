"""The stock desk: which stocks are in play right now, kept ready for the engine.

The indices are three instruments, and on a quiet day all three are quiet
together - 28 Sep 2026 had 14-19 tradable bars per index and no setup, while
PSU banks fell 4% on the same morning. This desk widens the net to the F&O
stock universe without widening the rules: it only decides WHICH stocks the
engine looks at (arb/scanner.py:rank), and the engine then trades them with
exactly the regime gate, playbooks, stops and exits it uses on the indices.

Data sources, chosen so that none of this competes with the engine for the
broker's rate-limited historical API:
  - live ticks for every liquid F&O cash stock, subscribed at startup and
    folded into 1-minute bars by arb/candles.py (volume from the exchange's own
    day counter, differenced)
  - NSE's daily bars for ATR%, average volume and turnover (arb/nse.py), read
    from the cache and topped up in the background
  - NSE's ban list, re-read once a day
  - Angel's 1-minute history only for the few stocks actually picked, so a
    fresh pick has enough structure to read (three calls, not two hundred)
"""
import json
import os
import threading
import time

from arb.instruments import IST, now_ist
from arb.scanner import daily_stat, rank, same_time_volume

HIST = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                    ".cache", "history", "stocks")


def _minute(iso):
    return int(iso[11:13]) * 60 + int(iso[14:16])


class StockDesk:
    def __init__(self, universe, slots, candles, L, api=None, nse=None, log=lambda *_: None):
        """`universe` {symbol: fo_universe entry}; `slots` {symbol: feed slot}."""
        self.universe = universe
        self.slots = dict(slots)
        self.candles = candles
        self.L = L
        self.api = api
        self.nse = nse
        self.log = log
        self.stats = {}
        self.same_time = {}
        self.banned = set()
        self.active = []            # [InPlay], best first
        self.last_pick = 0.0
        self.last_rank = []
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._seeded = set()

    # ── reference data ──────────────────────────────────────────────────────
    def load(self):
        """Daily stats from the cache, then the ban list. Safe to call again."""
        today = now_ist().strftime("%Y-%m-%d")
        stats, same = {}, {}
        for sym in self.universe:
            p = os.path.join(HIST, f"{sym}_1d.json")
            if not os.path.exists(p):
                continue
            try:
                rows = [r for r in json.load(open(p)) if r[0] < today]
            except (OSError, ValueError):
                continue
            st = daily_stat(sym, rows)
            if st is not None:
                stats[sym] = st
        with self._lock:
            self.stats = stats
        if self.nse is not None:
            try:
                self.banned = set(self.nse.ban_list())
            except Exception:
                pass
        self._ready.set()
        self.log(f"[stocks] {len(stats)}/{len(self.universe)} stocks with daily history; "
                 f"{len(self.banned)} in the F&O ban today")
        return len(stats)

    def top_up(self, max_age_days=3):
        """Fetch the last few sessions from NSE for stocks whose cache is stale.
        Runs on its own thread; the desk works from whatever is cached meanwhile."""
        if self.nse is None:
            return
        n = 0
        for sym in list(self.universe):
            p = os.path.join(HIST, f"{sym}_1d.json")
            try:
                old = json.load(open(p)) if os.path.exists(p) else []
            except (OSError, ValueError):
                old = []
            if old and time.time() - os.path.getmtime(p) < max_age_days * 86400 / 3:
                continue
            new = self.nse.daily(sym, days=370 if not old else 20)
            if not new:
                continue
            merged = {r[0]: r for r in old}
            merged.update({r[0]: r for r in new})
            os.makedirs(HIST, exist_ok=True)
            with open(p, "w") as fh:
                json.dump([merged[k] for k in sorted(merged)], fh)
            n += 1
        if n:
            self.log(f"[stocks] daily history topped up for {n} stocks")
            self.load()

    def start(self):
        def run():
            self.load()
            self.top_up()
        threading.Thread(target=run, name="stockdesk", daemon=True).start()
        return self

    # ── the scan ────────────────────────────────────────────────────────────
    def _live(self):
        """Today's open/high/low/last/volume per stock.

        From the exchange's own day figures on the tick (open/high/low of the
        day, cumulative day volume) when the feed has them, so a restart at
        14:30 still sees the whole session. The live bars are the fallback.
        """
        today = now_ist().strftime("%Y-%m-%d")
        out = {}
        st = getattr(self.candles, "_store", None)
        for sym, slot in self.slots.items():
            if st is not None and hasattr(st, "day_open"):
                o, h, lo = float(st.day_open[slot]), float(st.day_high[slot]), float(st.day_low[slot])
                px, vol = float(st.ltp[slot]), float(st.volume[slot])
                if min(o, h, lo, px) > 0 and vol > 0:
                    out[sym] = {"open": o, "high": h, "low": lo, "ltp": px, "volume": vol}
                    continue
            rows = [r for r in self.candles.get(sym, limit=400) if r[0][:10] == today]
            if not rows:
                continue
            out[sym] = {"open": rows[0][1], "high": max(r[2] for r in rows),
                        "low": min(r[3] for r in rows), "ltp": rows[-1][4],
                        "volume": sum(r[5] for r in rows)}
        return out

    def due(self, now=None):
        n = now or now_ist()
        hh, mm = (int(x) for x in str(self.L.stocks_pick_at).split(":"))
        if n.hour * 60 + n.minute < hh * 60 + mm:
            return False
        return time.time() - self.last_pick >= 60.0 * float(self.L.stocks_rescan_min)

    def pick(self, now=None, index_moves=None):
        """Re-rank the universe and keep the top K. Returns the new picks."""
        n = now or now_ist()
        if not self._ready.is_set():
            return self.active
        m = n.hour * 60 + n.minute
        with self._lock:
            stats = dict(self.stats)
        ranked = rank(stats, self._live(), m, self.L, banned=self.banned,
                      index_moves=index_moves, same_time=self.same_time_for(stats, m))
        self.last_rank = ranked[:15]
        self.active = ranked[:int(self.L.stocks_top_k)]
        self.last_pick = time.time()
        if not ranked:
            # Probably no quotes yet (just started): look again in a minute,
            # not at the next half-hour rescan.
            self.last_pick -= max(0.0, 60.0 * float(self.L.stocks_rescan_min) - 60.0)
        if self.active:
            self.log("[stocks] in play: " + "; ".join(
                f"{r.symbol} {r.bias} {100 * r.from_open:+.1f}% rvol {r.rvol:.1f} "
                f"ATR {100 * r.atr_pct:.1f}%" for r in self.active))
            want = [r.symbol for r in self.active if r.symbol not in self._seeded]
            if want:
                threading.Thread(target=self._seed_all, args=(want,),
                                 name="stock-seed", daemon=True).start()
        else:
            self.log("[stocks] nothing passes the scan filters")
        return self.active

    def same_time_for(self, stats, minute):
        """Average volume by this minute on recent sessions, where a stock has
        1-minute history on disk (tools/fetch_stocks.py minute)."""
        key = minute
        if self.same_time.get("_minute") == key:
            return self.same_time
        today = now_ist().strftime("%Y-%m-%d")
        out = {"_minute": key}
        for sym in stats:
            p = os.path.join(HIST, f"{sym}_1m.json")
            if not os.path.exists(p):
                continue
            try:
                v = same_time_volume(json.load(open(p)), today, minute)
            except (OSError, ValueError):
                continue
            if v > 0:
                out[sym] = v
        self.same_time = out
        return out

    def _seed_all(self, syms, gap_sec=2.0, tries=4, backoff_sec=20.0):
        """Seed picks off the engine thread, spaced and retried: the broker's
        historical API is shared with the index candles and refuses bursts."""
        for k in range(tries):
            left = [s for s in syms if s not in self._seeded]
            if not left:
                return
            if k:
                time.sleep(backoff_sec * k)
            for sym in left:
                self.seed(sym, quiet=k < tries - 1)
                time.sleep(gap_sec)

    def seed(self, sym, quiet=False):
        """Give a fresh pick the recent structure it needs, once per session.
        Returns True once seeded; a refusal leaves it to be retried."""
        if sym in self._seeded or self.api is None or not hasattr(self.candles, "seed"):
            return sym in self._seeded
        info = self.universe.get(sym) or {}
        try:
            end = now_ist()
            from datetime import timedelta
            r = self.api.getCandleData({
                "exchange": "NSE", "symboltoken": info.get("eq_token"),
                "interval": "ONE_MINUTE",
                "fromdate": (end - timedelta(days=4)).strftime("%Y-%m-%d 09:15"),
                "todate": end.strftime("%Y-%m-%d %H:%M")})
            if isinstance(r, dict) and r.get("status") is False:
                raise RuntimeError(f"{r.get('errorCode', '')} {r.get('message', '')}".strip())
            rows = (r or {}).get("data") or [] if isinstance(r, dict) else []
            rows = [[str(x[0]), float(x[1]), float(x[2]), float(x[3]), float(x[4]),
                     float(x[5] or 0)] for x in rows if x and len(x) >= 5]
            if rows:
                self.candles.seed(sym, rows)
                self._seeded.add(sym)
                self.log(f"[stocks] {sym}: {len(rows)} bars of history loaded")
                return True
        except Exception as e:
            if not quiet:
                rows = self._disk_history(sym)
                if rows:
                    self.candles.seed(sym, rows)
                    self._seeded.add(sym)
                    self.log(f"[stocks] {sym}: API refused ({str(e)[:60]}); "
                             f"{len(rows)} saved bars loaded from disk instead")
                    return True
                self.log(f"[stocks] {sym}: history seed failed ({str(e)[:90]}); "
                         f"trading on live bars only")
        return False

    @staticmethod
    def _disk_history(sym, bars=1500):
        """Recent 1-minute bars saved by tools/fetch_stocks.py minute, before today."""
        p = os.path.join(HIST, f"{sym}_1m.json")
        if not os.path.exists(p):
            return []
        today = now_ist().strftime("%Y-%m-%d")
        try:
            return [r for r in json.load(open(p)) if r[0][:10] < today][-bars:]
        except (OSError, ValueError):
            return []

    def summary(self):
        return {"active": [dict(symbol=r.symbol, bias=r.bias, score=round(r.score, 2),
                                move_pct=round(100 * r.move, 2),
                                from_open_pct=round(100 * r.from_open, 2),
                                rvol=round(r.rvol, 2), atr_pct=round(100 * r.atr_pct, 2),
                                range_atr=round(r.range_atr, 2), reasons=r.reasons)
                           for r in self.active],
                "ranked": [r.symbol for r in self.last_rank],
                "universe": len(self.slots), "with_stats": len(self.stats),
                "banned": sorted(self.banned)}
