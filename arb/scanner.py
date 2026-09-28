"""Which stocks are worth scalping today (research/PAPERS.md 0i).

The universe is every F&O stock, traded in the cash market intraday. The scan
ranks them by how much each is IN PLAY, up or down alike:

    ATR%          the stock's own 14-day range. On a year of NSE daily bars it
                  ranked the next day's range at rank correlation +0.43
                  in-sample and +0.40 held-out (t ~ 50): volatile stocks stay
                  volatile, which is what makes a morning list worth drawing up
    relative vol  volume so far against a normal day by the same minute
                  (Zarattini, Barbon & Aziz 2024: nearly all of their
                  opening-range edge came from this filter)
    move / range  today's move from the previous close and range so far, in ATRs

It does not pick a direction. Neither gaps nor yesterday's biggest movers
predicted the day's direction in both periods; the engine's own structure read
decides long or short, on the stock's own chart.
"""
from arb.instruments import parse_expiry


def fo_universe(scrip_master):
    """Every stock with NSE derivatives, mapped to its cash-market token.

    Only F&O stocks: they are the liquid names, the ones NSE's F&O lists and
    ban list cover, and the only ones that could ever be traded through options.
    Returns {symbol: {"eq_token", "eq_symbol", "lot", "fut_expiry"}}.
    """
    eq, fut = {}, {}
    for s in scrip_master:
        seg, itype, name = s.get("exch_seg"), s.get("instrumenttype"), s.get("name")
        if seg == "NSE" and s.get("symbol", "").endswith("-EQ"):
            eq[name] = s
        elif seg == "NFO" and itype == "FUTSTK" and "NSETEST" not in (name or ""):
            try:
                d = parse_expiry(s["expiry"])
            except Exception:
                continue
            if name not in fut or d < fut[name][0]:
                fut[name] = (d, s)
    out = {}
    for name, (_, f) in fut.items():
        e = eq.get(name)
        if e is None:
            continue
        out[name] = {"eq_token": str(e["token"]), "eq_symbol": e["symbol"],
                     "lot": int(float(f.get("lotsize", 0) or 0)),
                     "fut_expiry": f.get("expiry", "")}
    return out


# ── scoring ─────────────────────────────────────────────────────────────────
# What makes a stock worth scalping today is that it will MOVE, in either
# direction, by much more than the round trip costs. The scanner answers only
# that question. Which way to trade is left to the same structure read the
# indices use (arb/confluence.py), because on a year of NSE daily bars neither
# the opening gap nor yesterday's biggest movers predicted the direction of the
# day (research/PAPERS.md 0i), while a stock's own recent range predicted
# today's range strongly and stably (rank correlation ~0.4, in-sample and
# held-out alike).
from dataclasses import dataclass, field
import math


@dataclass
class DailyStat:
    """What is known about a stock before the open, from its daily bars."""
    symbol: str
    prev_close: float
    atr_pct: float            # 14-day average true range, % of price
    adv: float                # average daily volume, shares (20 days)
    turnover_cr: float        # median daily turnover, Rs crore (20 days)
    beta: float = 0.0         # to its index, 60 days (0 = unknown)
    index: str = ""


def daily_stat(symbol, rows, index_closes=None, index="", atr_days=14):
    """DailyStat from NSE daily rows [date, o, h, l, c, vol, turnover, deliv%],
    oldest first, ending with the last COMPLETED session. None if too short."""
    if len(rows) < atr_days + 1:
        return None
    tr = []
    for p, r in zip(rows[-atr_days - 1:-1], rows[-atr_days:]):
        pc = p[4]
        tr.append(max(r[2] - r[3], abs(r[2] - pc), abs(r[3] - pc)) / pc)
    last = rows[-20:]
    turn = sorted(r[6] for r in last)
    beta = 0.0
    if index_closes:
        xs, ys = [], []
        for p, r in zip(rows[-61:-1], rows[-60:]):
            a, b = index_closes.get(p[0]), index_closes.get(r[0])
            if a and b:
                xs.append(b / a - 1); ys.append(r[4] / p[4] - 1)
        if len(xs) >= 30:
            mx, my = sum(xs) / len(xs), sum(ys) / len(ys)
            vx = sum((x - mx) ** 2 for x in xs)
            if vx > 0:
                beta = sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / vx
    return DailyStat(symbol, rows[-1][4], sum(tr) / len(tr),
                     sum(r[5] for r in last) / len(last),
                     turn[len(turn) // 2] / 1e7, beta, index)


def volume_done(minute, open_min=555, close_min=930, a=3.0, b=1.5, tau=20.0):
    """Share of a normal day's volume done by `minute` (minutes since midnight).

    NSE cash volume is U-shaped: heavy at the open, thin at midday, heavy into
    the close. Modelled as intensity 1 + a e^(-t/tau) + b e^(-(T-t)/tau) and
    integrated, so relative volume early in the day is not judged against a
    flat clock. The shape parameters are a prior; tools/stock_study.py can fit
    them from 1-minute bars.
    """
    T = float(close_min - open_min)
    t = max(0.0, min(T, float(minute - open_min)))
    F = lambda x: x + a * tau * (1 - math.exp(-x / tau)) + b * tau * (math.exp(-(T - x) / tau) - math.exp(-T / tau))
    return max(1e-3, F(t) / F(T))


@dataclass
class InPlay:
    symbol: str
    score: float
    price: float
    atr_pct: float
    gap: float                # open / previous close - 1
    move: float               # last / previous close - 1
    from_open: float          # last / open - 1
    range_atr: float          # today's range so far, in ATRs
    rvol: float               # volume so far / what a normal day has done by now
    rs: float = 0.0           # move beyond what beta x index move explains
    reasons: list = field(default_factory=list)

    @property
    def bias(self):
        """Which way it is moving today - reported, not traded on."""
        return "up" if self.from_open > 0 else "down" if self.from_open < 0 else "flat"


def _pct_rank(vals):
    order = sorted(range(len(vals)), key=lambda i: vals[i])
    out = [0.0] * len(vals)
    for r, i in enumerate(order):
        out[i] = r / max(1, len(vals) - 1)
    return out


def same_time_volume(minute_rows, day, upto_min, sessions=14):
    """Average volume traded from the open to `upto_min` over the last
    `sessions` sessions before `day` - the denominator of Zarattini, Barbon &
    Aziz's relative volume. `minute_rows` are 1-minute [iso, o, h, l, c, v]."""
    per = {}
    for r in minute_rows:
        d = r[0][:10]
        if d >= day:
            break
        m = int(r[0][11:13]) * 60 + int(r[0][14:16])
        if m < upto_min:
            per[d] = per.get(d, 0.0) + float(r[5])
    vals = [per[d] for d in sorted(per)[-sessions:]]
    return sum(vals) / len(vals) if len(vals) >= max(3, sessions // 2) else 0.0


def rank(stats, live, minute, L, banned=(), index_moves=None, same_time=None):
    """Rank the universe by how much each stock is in play right now.

    `stats` {symbol: DailyStat}; `live` {symbol: {"open","high","low","ltp",
    "volume"}} for today; `minute` minutes since midnight IST; `index_moves`
    {index: move since previous close} for relative strength. `same_time`
    {symbol: average volume by this minute on recent sessions} gives the
    paper's relative volume; without it the U-shaped profile x ADV stands in.
    Returns [InPlay] best first, already filtered for liquidity, price and ban.
    """
    rows = []
    banned = set(banned or ())
    for sym, st in stats.items():
        q = live.get(sym)
        if not q or st is None or sym in banned:
            continue
        o, h, lo, px, vol = (float(q.get(k) or 0.0) for k in ("open", "high", "low", "ltp", "volume"))
        if min(o, h, lo, px, st.prev_close) <= 0 or st.atr_pct <= 0:
            continue
        if px < float(getattr(L, "stocks_min_price", 50.0)):
            continue
        if st.turnover_cr < float(getattr(L, "stocks_min_turnover_cr", 50.0)):
            continue
        if st.atr_pct * 100 < float(getattr(L, "stocks_min_atr_pct", 1.5)):
            continue
        atr = st.atr_pct * st.prev_close
        base = (same_time or {}).get(sym) or (st.adv * volume_done(minute) if st.adv > 0 else 0.0)
        rvol = vol / base if base > 0 else 0.0
        move = px / st.prev_close - 1
        rs = 0.0
        if index_moves and st.index in index_moves and st.beta:
            rs = move - st.beta * index_moves[st.index]
        rows.append(InPlay(sym, 0.0, px, st.atr_pct, o / st.prev_close - 1, move,
                           px / o - 1, (h - lo) / atr, rvol, rs))
    if not rows:
        return []
    w = (float(getattr(L, "stocks_w_atr", 1.0)), float(getattr(L, "stocks_w_rvol", 1.0)),
         float(getattr(L, "stocks_w_move", 0.5)), float(getattr(L, "stocks_w_range", 0.5)))
    feats = ([r.atr_pct for r in rows], [r.rvol for r in rows],
             [abs(r.move) / r.atr_pct for r in rows], [r.range_atr for r in rows])
    ranks = [_pct_rank(f) for f in feats]
    names = ("atr", "rvol", "move", "range")
    for i, r in enumerate(rows):
        r.score = sum(wk * rk[i] for wk, rk in zip(w, ranks)) / max(sum(w), 1e-9)
        r.reasons = [f"{n} p{100 * rk[i]:.0f}" for n, rk, wk in zip(names, ranks, w) if wk and rk[i] >= 0.8]
    min_rvol = float(getattr(L, "stocks_min_rvol", 0.0))
    rows = [r for r in rows if r.rvol >= min_rvol]
    rows.sort(key=lambda r: -r.score)
    return rows
