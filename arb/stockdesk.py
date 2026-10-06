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
from collections import deque

from arb.instruments import IST, now_ist
from arb.scanner import InPlay, daily_stat, rank, same_time_volume
from arb.stockspike import spike

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
        self._same_key = None
        self._same = ({}, {})
        # Once-a-minute snapshots of every stock's cumulative day volume, so
        # relative volume can be measured over a recent window, not only since
        # the open. Kept just long enough to reach one window back.
        window = float(getattr(L, "stocks_rvol_window_min", 15.0))
        self._vol_snaps = deque(maxlen=max(0, int(window)) + 5)
        self._last_snap = 0.0
        self.banned = set()
        self.active = []            # [InPlay], best first
        self.last_pick = 0.0
        self.last_rank = []
        self._lock = threading.Lock()
        self._ready = threading.Event()
        self._seeded = set()
        # Picks trading on saved bars (before today) because the API refused
        # them. Not "seeded": today's bars are still missing, so the next
        # rescan asks the API again rather than leaving the gap all session.
        self._disk_only = set()
        # Per-minute spike watch over EVERY subscribed stock (owner, 6 Oct 2026):
        # a stock whose range explodes between two 30-minute rescans joins the
        # picks at once and stays for stocks_spike_hold_min.
        self._picks = []            # the last rescan's top K
        self._spikes = {}           # symbol -> (InPlay, expires_at)
        self._spike_minute = ""
        self.last_spikes = []       # [(symbol, ratio, move)] of the last scan, for the log/page
        # NSE's live top gainers / losers (F&O), refreshed in the background.
        self.movers = {}            # symbol -> (list, % change, rank)
        self._movers_at = 0.0
        self._movers_busy = False

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
                    out[sym] = {"open": o, "high": h, "low": lo, "ltp": px, "volume": vol,
                                "recent_volume": self._recent_volume(sym, vol)}
                    continue
            rows = [r for r in self.candles.get(sym, limit=400) if r[0][:10] == today]
            if not rows:
                continue
            out[sym] = {"open": rows[0][1], "high": max(r[2] for r in rows),
                        "low": min(r[3] for r in rows), "ltp": rows[-1][4],
                        "volume": sum(r[5] for r in rows)}
        return out

    def _snap(self, t=None):
        """Record every stock's cumulative day volume, at most once a minute."""
        t = time.time() if t is None else t
        if t - self._last_snap < 60.0:
            return
        st = getattr(self.candles, "_store", None)
        if st is None or not hasattr(st, "volume"):
            return
        self._last_snap = t
        self._vol_snaps.append((t, {s: float(st.volume[slot]) for s, slot in self.slots.items()}))

    def _recent_volume(self, sym, cumulative, t=None):
        """Volume over the last `stocks_rvol_window_min` minutes, or None when
        there is no snapshot that old yet (just started) or it was taken before
        the stock's first tick - an unknown, never a zero."""
        window = float(getattr(self.L, "stocks_rvol_window_min", 15.0))
        if window <= 0:
            return None
        t = time.time() if t is None else t
        cutoff = t - 60.0 * window
        past = None
        for ts, vols in self._vol_snaps:
            if ts > cutoff:
                break
            past = vols
        v0 = (past or {}).get(sym, 0.0)
        if v0 <= 0 or cumulative < v0:
            return None
        return cumulative - v0

    def due(self, now=None):
        # The engine calls due() outside its stock-scan error handling, on the
        # same pass that hunts the indices: nothing stock-side may raise here.
        try:
            self._snap()
        except Exception:
            pass
        n = now or now_ist()
        hh, mm = (int(x) for x in str(self.L.stocks_pick_at).split(":"))
        if n.hour * 60 + n.minute < hh * 60 + mm:
            return False
        return time.time() - self.last_pick >= 60.0 * float(self.L.stocks_rescan_min)

    def pick(self, now=None, index_moves=None, cost_ratio=None):
        """Re-rank the universe and keep the top K. Returns the new picks.

        `cost_ratio` is a callable symbol -> scan_cost_ratio (or None), supplied
        by the engine, which holds the capital and cost model it needs."""
        n = now or now_ist()
        if not self._ready.is_set():
            return self.active
        m = n.hour * 60 + n.minute
        with self._lock:
            stats = dict(self.stats)
        live = self._live()
        costs = {}
        if cost_ratio is not None:
            for sym in live:
                try:
                    c = cost_ratio(sym)
                except Exception:
                    c = None
                if c is not None:
                    costs[sym] = c
        same, same_recent = self.same_time_for(stats, m)
        ranked = rank(stats, live, m, self.L, banned=self.banned,
                      index_moves=index_moves, same_time=same,
                      same_time_recent=same_recent, cost_ratio=costs)
        self.last_rank = ranked[:15]
        self._picks = ranked[:int(self.L.stocks_top_k)]
        self._merge_spikes()
        self.last_pick = time.time()
        if not ranked:
            # Probably no quotes yet (just started): look again in a minute,
            # not at the next half-hour rescan.
            self.last_pick -= max(0.0, 60.0 * float(self.L.stocks_rescan_min) - 60.0)
        if self._picks:
            self.log("[stocks] in play: " + "; ".join(
                f"{r.symbol} {r.bias} {100 * r.from_open:+.1f}% rvol {r.rvol:.1f} "
                f"ATR {100 * r.atr_pct:.1f}%"
                + (f" cost {r.cost_ratio:.2f}" if r.cost_ratio is not None else "")
                for r in self._picks))
            want = [r.symbol for r in self._picks if r.symbol not in self._seeded]
            if want:
                threading.Thread(target=self._seed_all, args=(want,),
                                 name="stock-seed", daemon=True).start()
        else:
            f = getattr(rank, "last_funnel", None) or {}
            self.log("[stocks] nothing passes the scan filters"
                     + (" (" + ", ".join(f"{k} {v}" for k, v in f.items()) + ")" if f else ""))
        return self.active

    # ── the per-minute spike watch ──────────────────────────────────────────
    def _merge_spikes(self, now=None):
        """active = the rescan's picks + live spikes not already among them."""
        t = time.time() if now is None else now
        self._spikes = {s: v for s, v in self._spikes.items() if v[1] > t}
        names = {r.symbol for r in self._picks}
        self.active = list(self._picks) + [v[0] for s, v in sorted(
            self._spikes.items(), key=lambda kv: -kv[1][0].score) if s not in names]

    def _closed_rows(self, sym, n, limit=200):
        """This stock's CLOSED one-minute bars (the bar of minute `n` is still forming)."""
        cur = n.strftime("%Y-%m-%dT%H:%M")
        return [r for r in self.candles.get(sym, limit=limit) if r[0][:16] < cur]

    def refresh_movers(self):
        """NSE's top gainers and losers (F&O), on a background thread, at most
        every stocks_movers_min. Abstains (keeps the last list) on any failure."""
        every = 60.0 * float(getattr(self.L, "stocks_movers_min", 5.0) or 0.0)
        if self.nse is None or every <= 0 or self._movers_busy or time.time() - self._movers_at < every:
            return

        def work():
            try:
                out = {}
                for side in ("gainers", "losers"):
                    for k, x in enumerate(self.nse.movers(side) or []):
                        sym = x.get("symbol")
                        if sym:
                            out[sym] = (side, float(x.get("perChange") or 0.0), k + 1)
                if out:
                    self.movers = out
            except Exception:
                pass
            finally:
                self._movers_at = time.time()
                self._movers_busy = False

        self._movers_busy = True
        threading.Thread(target=work, name="nse-movers", daemon=True).start()

    def spike_tick(self, now=None, cost_ratio=None):
        """Once a minute (just after the bar closes): every subscribed stock's
        short-window ATR against its own recent normal. A stock at or above
        stocks_spike_ratio joins the active picks for stocks_spike_hold_min -
        NSE top gainers/losers first, then the biggest spikes. Returns the new
        spike names. stocks_spike_watch: "on" feeds the engine, "shadow" only
        logs, anything else is off."""
        mode = str(getattr(self.L, "stocks_spike_watch", "off"))
        if mode not in ("on", "shadow") or not self._ready.is_set():
            return []
        n = now or now_ist()
        key = n.strftime("%H:%M")
        if key == self._spike_minute or n.second < 3:
            return []
        m = n.hour * 60 + n.minute
        hh, mm = (int(x) for x in str(self.L.stocks_pick_at).split(":"))
        if m < hh * 60 + mm or m >= 15 * 60:          # no new stock entries from 15:00
            return []
        self._spike_minute = key
        self.refresh_movers()
        L = self.L
        thr = float(getattr(L, "stocks_spike_ratio", 2.5))
        fast = int(getattr(L, "stocks_spike_fast", 5))
        base = int(getattr(L, "stocks_spike_base_bars", 120))
        max_cost = float(getattr(L, "stocks_max_cost_ratio", 1.0))
        with self._lock:
            stats = dict(self.stats)
        found = []
        for sym in self.slots:
            st = stats.get(sym)
            if st is None or sym in self.banned:
                continue
            if st.prev_close < float(getattr(L, "stocks_min_price", 100.0)) \
                    or st.turnover_cr < float(getattr(L, "stocks_min_turnover_cr", 50.0)):
                continue
            rows = self._closed_rows(sym, n, limit=base + fast + 2)
            r = spike(rows, fast=fast, base=base)
            if r is None or r[0] < thr:
                continue
            cr = None
            if cost_ratio is not None:
                try:
                    cr = cost_ratio(sym)
                except Exception:
                    cr = None
                if cr is not None and max_cost > 0 and cr > max_cost:
                    continue
            found.append((sym, r[0], r[1], cr, rows[-1][4], st))
        # NSE's movers first (the owner's "most positive / most negative"), then size of spike.
        found.sort(key=lambda f: (f[0] not in self.movers, -f[1]))
        self.last_spikes = [(f[0], round(f[1], 2), round(f[2], 4)) for f in found[:15]]
        k = int(getattr(L, "stocks_spike_k", 3))
        hold = 60.0 * float(getattr(L, "stocks_spike_hold_min", 15.0))
        picks = {r.symbol for r in self._picks}
        new = []
        for sym, ratio, move, cr, px, st in found:
            if len(new) >= k:
                break
            if sym in picks:
                continue
            mv = self.movers.get(sym)
            tag = f" NSE {mv[0][:-1]} #{mv[2]} {mv[1]:+.1f}%" if mv else ""
            ip = InPlay(sym, ratio, px, st.atr_pct, 0.0, px / st.prev_close - 1 if st.prev_close else 0.0,
                        move, 0.0, 0.0, cost_ratio=cr, reasons=[f"spike {ratio:.1f}x" + tag])
            fresh = sym not in self._spikes
            if mode == "on":
                self._spikes[sym] = (ip, time.time() + hold)
            if fresh:
                new.append(sym)
                self.log(f"[stocks] spike{' (shadow)' if mode != 'on' else ''}: {sym} "
                         f"{ratio:.1f}x its normal 1-min range, {100 * move:+.2f}% in {fast} min"
                         f"{tag}" + (f" cost {cr:.2f}" if cr is not None else ""))
        if mode == "on":
            self._merge_spikes()
            want = [s for s in new if s not in self._seeded]
            if want:
                threading.Thread(target=self._seed_all, args=(want,),
                                 name="stock-seed", daemon=True).start()
        return new

    def same_time_for(self, stats, minute):
        """Average volume on recent sessions, where a stock has 1-minute history
        on disk (tools/fetch_stocks.py minute): from the open to this minute,
        and over the recent window ending at it. Returns (since_open, window)."""
        if self._same_key == minute:
            return self._same
        window = float(getattr(self.L, "stocks_rvol_window_min", 15.0))
        today = now_ist().strftime("%Y-%m-%d")
        cum, rec = {}, {}
        for sym in stats:
            p = os.path.join(HIST, f"{sym}_1m.json")
            if not os.path.exists(p):
                continue
            try:
                rows = json.load(open(p))
            except (OSError, ValueError):
                continue
            v = same_time_volume(rows, today, minute)
            if v > 0:
                cum[sym] = v
            if window > 0:
                w = same_time_volume(rows, today, minute, from_min=minute - window)
                if w > 0:
                    rec[sym] = w
        self._same_key, self._same = minute, (cum, rec)
        return self._same

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
            from arb.candles import candle_data
            r = candle_data(self.api, {
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
                self._disk_only.discard(sym)
                self.log(f"[stocks] {sym}: {len(rows)} bars of history loaded")
                return True
        except Exception as e:
            if not quiet:
                if sym in self._disk_only:
                    return True        # already on saved bars; the next rescan asks again
                rows = self._disk_history(sym)
                if rows:
                    self.candles.seed(sym, rows)
                    self._disk_only.add(sym)
                    self.log(f"[stocks] {sym}: API refused ({str(e)[:60]}); "
                             f"{len(rows)} saved bars loaded from disk instead, "
                             f"today's asked for again at the next rescan")
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
                                rvol=round(r.rvol, 2), rvol_day=round(r.rvol_day, 2),
                                atr_pct=round(100 * r.atr_pct, 2),
                                range_atr=round(r.range_atr, 2),
                                cost_ratio=(round(r.cost_ratio, 2)
                                            if r.cost_ratio is not None else None),
                                reasons=r.reasons)
                           for r in self.active],
                "ranked": [r.symbol for r in self.last_rank],
                "spikes": list(self.last_spikes),
                "movers": sorted(([s, v[0], v[1]] for s, v in self.movers.items()),
                                 key=lambda x: -abs(x[2]))[:10],
                "universe": len(self.slots), "with_stats": len(self.stats),
                "banned": sorted(self.banned)}
