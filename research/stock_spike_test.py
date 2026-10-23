#!/usr/bin/env python3
"""Watch every liquid F&O stock, trade a sudden ATR spike (owner's request, 6 Oct 2026).

    nice -n 10 venv/bin/python research/stock_spike_test.py build      # 1m/1d JSON -> numpy grids
    nice -n 10 venv/bin/python research/stock_spike_test.py features   # spikes, movers, controls, desk
    nice -n 10 venv/bin/python research/stock_spike_test.py cands      # engine gates + exits on marked bars
    nice -n 10 venv/bin/python research/stock_spike_test.py report     # every variant -> stock_spike_result.txt

Offline only: Angel 1-minute bars and NSE daily bars already in .cache/history/stocks.
Scratch goes to research_out/stocks_1006/.

What is tested
  spike   every minute, on every liquid stock (price >= 100, 20-day median turnover
          >= Rs 50 cr, from the daily bars before the day), with data up to that
          minute only:
            A  ATR5 / median true range of the 200 bars before it   >= 2, 2.5, 3
            B  15-minute range / the same 15 minutes' average range on the prior
               (up to) 14 sessions                                   >= 2, 2.5, 3
          plus relative volume: last 5 minutes' volume / the same 5 minutes on the
          prior 14 sessions >= 0 (off), 1.5, 2, 3.
  movers  'top gainers / losers' proxy: rank of |last / previous close - 1| among the
          liquid stocks at that minute; all, top 20, top 10.
  entries (each with the engine's own gates and exits, tools/stock_backtest.py costs)
            smc1/smc3/smc5  arb/confluence.evaluate (ob_fvg_retest) on 1/3/5-minute
                            bars (3/5 resampled from 1m, aligned to 09:15), within W
                            minutes of a spike (W = 30, 60)
            mom_spike       at the spike bar's close, in the 5-bar move's direction,
                            stop beyond the extreme of the last 5 bars (+0.1 ATR pad)
            mom_swing1      same entry, stop at the last 1-minute swing (structure)
            mom_swing5      same entry, stop at the last 5-minute swing
  gates   regime_gate(name='STOCKS') on the entry timeframe, evaluate (smc), stop >=
          min_stop_atr x ATR, size = risk cap with costs inside (bisection, as
          IntradayEngine._stock_qty) and capital x 5 / price, cost <= 20% of risk,
          momentum target >= min_target_r, net at target >= stocks_min_net_inr.
  book    one position at a time across all stocks, 15 trades/day, 15% daily loss
          halt, 3 losses -> 30 min pause then grade B+ until a win, 180 s after a
          loss, 300 s per symbol, entries 09:30-14:59, square-off at 15:14.
  controls the same entry at the same minutes on a random NON-spiking liquid stock;
          the live desk (scanner top 5 at 09:30, re-picked every 30 min) with smc1.
"""
import glob, json, os, pickle, sys, time
from datetime import datetime
from multiprocessing import Pool

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))
import numpy as np

H = os.path.join(ROOT, ".cache", "history", "stocks")
OUT = os.path.join(ROOT, "research_out", "stocks_1006")
GRID = os.path.join(OUT, "grid")
RESULT = os.path.join(ROOT, "research", "stock_spike_result.txt")
SPLIT = "2026-08-10"
NK = 375                       # 09:15 .. 15:29
K0 = 9 * 60 + 15
CAPITAL = 10672.74             # the live account's capital on 6 Oct 2026 (owner)
K_FIRST = 14                   # bar 09:29 closes at 09:30 (warmup 15 min)
K_LAST = 343                   # bar 14:58 closes at 14:59 (no new trades at 15:00)
K_SQ = 238 + 120               # 1m bar 15:13 closes at 15:14 -> square-off
TFS = (1, 3, 5)
W_MAX = 60


def iso_of(day, k):
    m = K0 + k
    return f"{day}T{m // 60:02d}:{m % 60:02d}:00+05:30"


# ════════════════════════════════════════════════════════════════════════════
# 1. build: JSON -> per-stock (D, 375, 5) float64 grids, NaN where a bar is missing
# ════════════════════════════════════════════════════════════════════════════
def build():
    os.makedirs(GRID, exist_ok=True)
    names = sorted(os.path.basename(p)[:-8] for p in glob.glob(f"{H}/*_1m.json"))
    names = [s for s in names if os.path.exists(f"{H}/{s}_1d.json")]
    days = set()
    for s in names:
        rows = json.load(open(f"{H}/{s}_1m.json"))
        days.update(r[0][:10] for r in rows)
        del rows
    days = sorted(days)
    di = {d: i for i, d in enumerate(days)}
    D = len(days)
    pc = np.full((len(names), D), np.nan); atrp = np.full((len(names), D), np.nan)
    turn = np.full((len(names), D), np.nan); adv = np.full((len(names), D), np.nan)
    from arb.scanner import daily_stat
    missing = 0
    for si, s in enumerate(names):
        g = np.full((D, NK, 5), np.nan)
        for r in json.load(open(f"{H}/{s}_1m.json")):
            k = int(r[0][11:13]) * 60 + int(r[0][14:16]) - K0
            if 0 <= k < NK:
                g[di[r[0][:10]], k] = r[1:6]
        np.save(f"{GRID}/{s}.npy", g)
        daily = json.load(open(f"{H}/{s}_1d.json"))
        for d, i in di.items():
            prior = [r for r in daily if r[0] < d]
            st = daily_stat(s, prior)
            if st is not None:
                pc[si, i], atrp[si, i], turn[si, i], adv[si, i] = st.prev_close, st.atr_pct, st.turnover_cr, st.adv
        have = ~np.isnan(g[:, :, 3]).all(axis=1)
        missing += int(np.isnan(g[have][:, :, 3]).sum())
    np.savez(f"{OUT}/meta.npz", names=np.array(names), days=np.array(days), pc=pc, atrp=atrp, turn=turn, adv=adv)
    print(f"{len(names)} stocks x {D} sessions ({days[0]} .. {days[-1]}); missing 1m bars on traded days: {missing}")


def load_meta():
    z = np.load(f"{OUT}/meta.npz")
    return [str(x) for x in z["names"]], [str(x) for x in z["days"]], z["pc"], z["atrp"], z["turn"], z["adv"]


# ════════════════════════════════════════════════════════════════════════════
# 2. features: spike metrics, movers rank, liquidity, controls, desk picks, marks
# ════════════════════════════════════════════════════════════════════════════
def _sametime_base(x, n=14, need=5):
    """x (D, NK): mean of the previous <= n sessions at the same minute (NaN until `need`)."""
    D = x.shape[0]
    out = np.full_like(x, np.nan)
    for d in range(D):
        lo = max(0, d - n)
        prev = x[lo:d]
        if prev.shape[0] == 0:
            continue
        cnt = (~np.isnan(prev)).sum(axis=0)
        with np.errstate(invalid="ignore", divide="ignore"):
            m = np.nansum(prev, axis=0) / cnt
        m[cnt < need] = np.nan
        out[d] = m
    return out


def stock_features(g):
    """Per-stock (D, NK) arrays, each computed from bars up to and including that minute."""
    D = g.shape[0]
    o, h, l, c, v = (g[:, :, i] for i in range(5))
    flat_ok = ~np.isnan(c.ravel())
    idx = np.flatnonzero(flat_ok)
    hf, lf, cf = h.ravel()[idx], l.ravel()[idx], c.ravel()[idx]
    pcf = np.r_[np.nan, cf[:-1]]
    tr = np.fmax(hf - lf, np.fmax(np.abs(hf - pcf), np.abs(lf - pcf)))
    tr[0] = hf[0] - lf[0]
    n = len(tr)
    atr5 = np.full(n, np.nan); base = np.full(n, np.nan); dir5 = np.zeros(n)
    if n > 210:
        cs = np.r_[0, np.cumsum(tr)]
        atr5[4:] = (cs[5:] - cs[:-5]) / 5
        from numpy.lib.stride_tricks import sliding_window_view
        win = sliding_window_view(tr, 200)            # win[j] = tr[j .. j+199]
        med = np.median(win, axis=1)
        # baseline for bar i = tr[i-204 .. i-5] = win[i-204]
        base[204:] = med[:n - 204]
        dir5[5:] = np.sign(cf[5:] - cf[:-5])
    a5r = np.full(D * NK, np.nan); a5r[idx] = atr5 / np.where(base > 0, base, np.nan)
    d5 = np.zeros(D * NK); d5[idx] = dir5
    # 15-minute range and 5-minute volume, within the day, vs the same minutes on prior sessions
    from numpy.lib.stride_tricks import sliding_window_view as sw
    r15 = np.full((D, NK), np.nan)
    hh = np.nanmax(sw(h, 15, axis=1), axis=2)
    ll = np.nanmin(sw(l, 15, axis=1), axis=2)
    r15[:, 14:] = hh - ll
    v5 = np.full((D, NK), np.nan)
    v5[:, 4:] = np.nansum(sw(np.nan_to_num(v), 5, axis=1), axis=2)
    v5[np.isnan(c)] = np.nan
    r15b = _sametime_base(r15); v5b = _sametime_base(v5)
    with np.errstate(invalid="ignore", divide="ignore"):
        r15r = r15 / r15b
        rv5 = v5 / v5b
    return a5r.reshape(D, NK).astype(np.float32), r15r.astype(np.float32), rv5.astype(np.float32), \
        d5.reshape(D, NK).astype(np.int8)


def features():
    import warnings; warnings.filterwarnings("ignore")
    names, days, pc, atrp, turn, adv = load_meta()
    S, D = len(names), len(days)
    A5 = np.zeros((S, D, NK), np.float32); R15 = np.zeros((S, D, NK), np.float32)
    RV = np.zeros((S, D, NK), np.float32); DIR = np.zeros((S, D, NK), np.int8)
    MV = np.zeros((S, D, NK), np.float32); OK = np.zeros((S, D, NK), bool)
    for si, s in enumerate(names):
        g = np.load(f"{GRID}/{s}.npy")
        A5[si], R15[si], RV[si], DIR[si] = stock_features(g)
        c = g[:, :, 3]
        OK[si] = ~np.isnan(c)
        with np.errstate(invalid="ignore"):
            MV[si] = (c / pc[si][:, None] - 1).astype(np.float32)
    liquid = (pc >= 100) & (turn >= 50)                      # (S, D), known before the open
    LIQ = liquid[:, :, None] & OK
    absmv = np.where(LIQ, np.abs(np.nan_to_num(MV)), -1.0)
    order = np.argsort(-absmv, axis=0, kind="stable")
    RANK = np.empty((S, D, NK), np.int16)
    np.put_along_axis(RANK, order, np.arange(1, S + 1, dtype=np.int16)[:, None, None], axis=0)
    RANK[~LIQ] = 999
    kk = np.arange(NK)
    tradable_k = (kk >= K_FIRST) & (kk <= K_LAST)
    A5n, R15n = np.nan_to_num(A5), np.nan_to_num(R15)
    LOOSE = LIQ & tradable_k[None, None, :] & ((A5n >= 2.0) | (R15n >= 2.0))
    QUIET = LIQ & (A5n < 1.5) & (R15n < 1.5) & (A5n > 0)     # 'not spiking' for the control draw
    # Control stock for every loose spike bar: a random quiet liquid stock at the same minute.
    rng = np.random.default_rng(7)
    ev = np.argwhere(LOOSE)                                   # (n, 3) s, d, k
    ctrl = np.full(len(ev), -1, np.int32)
    by_dk = {}
    for j, (s, d, k) in enumerate(ev):
        by_dk.setdefault((d, k), []).append(j)
    for (d, k), js in by_dk.items():
        pool = np.flatnonzero(QUIET[:, d, k])
        if len(pool):
            ctrl[js] = rng.choice(pool, size=len(js))
    # Desk: scanner top 5 at 09:30 and every 30 min, traded for the next 30 minutes.
    desk = desk_picks(names, days, pc, atrp, turn, adv)
    DESK = np.zeros((S, D, NK), bool)
    for (d, k0), syms in desk.items():
        for s in syms:
            DESK[names.index(s), d, k0:min(k0 + 30, K_LAST + 1)] = True
    # Bars to evaluate. SMC: W_MAX after a loose spike (spiking stock and its control),
    # every bar of the top-20 movers, and the desk's picks. Momentum: loose spike bars
    # and their control bars.
    MARK = np.zeros((S, D, NK), bool)
    for w in range(W_MAX + 1):
        k = np.minimum(ev[:, 2] + w, NK - 1)
        MARK[ev[:, 0], ev[:, 1], k] = True
        okc = ctrl >= 0
        MARK[ctrl[okc], ev[okc, 1], k[okc]] = True
    MARK |= (RANK <= 20) & tradable_k[None, None, :]
    MARK |= DESK
    MARK &= tradable_k[None, None, :] & OK
    # Momentum enters when a threshold is CROSSED (the bar the ATR 'goes high all of a sudden').
    CROSS = cross_mask(A5n, R15n) & LOOSE
    MOM = CROSS.copy()
    okc = (ctrl >= 0) & CROSS[ev[:, 0], ev[:, 1], ev[:, 2]]
    MOM[ctrl[okc], ev[okc, 1], ev[okc, 2]] = True
    np.savez_compressed(f"{OUT}/features.npz", A5=A5, R15=R15, RV=RV, DIR=DIR, MV=MV, RANK=RANK, LIQ=LIQ,
                        ev=ev, ctrl=ctrl, DESK=DESK, MARK=MARK, MOM=MOM)
    pickle.dump(desk, open(f"{OUT}/desk.pkl", "wb"))
    n_liq = LIQ[:, :, tradable_k].sum()
    print(f"liquid stock-minutes {n_liq:,}; loose spikes {LOOSE.sum():,} ({100 * LOOSE.sum() / n_liq:.1f}%)")
    for th in (2.0, 2.5, 3.0):
        a = (LIQ & tradable_k & (A5n >= th)).sum(); b = (LIQ & tradable_k & (R15n >= th)).sum()
        print(f"  A>= {th}: {a:,} ({100 * a / n_liq:.2f}%)   B>= {th}: {b:,} ({100 * b / n_liq:.2f}%)")
    print(f"marked for smc: {MARK.sum():,} stock-minutes; momentum bars {MOM.sum():,}; desk slots {len(desk)}")


def crossed(x, th):
    """x (S, D, NK): the bar where x reaches th from below (previous bar of the same day < th)."""
    prev = np.zeros_like(x); prev[:, :, 1:] = x[:, :, :-1]
    return (x >= th) & (prev < th)


def cross_mask(A5n, R15n):
    out = np.zeros(A5n.shape, bool)
    for th in (2.0, 2.5, 3.0):
        out |= crossed(A5n, th) | crossed(R15n, th)
    return out


def desk_picks(names, days, pc, atrp, turn, adv):
    """The live desk (arb/stockdesk.py): arb/scanner.rank at 09:30, then every 30 min."""
    from arb.scanner import DailyStat, rank, scan_cost_ratio
    from arb.config import CostModel, IntradayLimits
    L = IntradayLimits.load(); CM = CostModel.load()
    S, D = len(names), len(days)
    G = {}
    out = {}
    times = [K0 + 15 + 30 * i for i in range(11)]           # 09:30 .. 14:30
    grids = {s: np.load(f"{GRID}/{s}.npy", mmap_mode="r") for s in names}
    win = int(getattr(L, "stocks_rvol_window_min", 15))
    for d in range(D):
        for T in times:
            kT = T - K0                                      # bars 0..kT-1 are closed
            stats, live, same, same_r, cr = {}, {}, {}, {}, {}
            for si, s in enumerate(names):
                if np.isnan(pc[si, d]):
                    continue
                g = grids[s]
                day = g[d, :kT]
                ok = ~np.isnan(day[:, 3])
                if ok.sum() < 5:
                    continue
                dd = day[ok]
                stats[s] = DailyStat(s, float(pc[si, d]), float(atrp[si, d]), float(adv[si, d]), float(turn[si, d]))
                rec = np.nansum(day[max(0, kT - win):kT, 4])
                live[s] = {"open": float(dd[0, 0]), "high": float(dd[:, 1].max()), "low": float(dd[:, 2].min()),
                           "ltp": float(dd[-1, 3]), "volume": float(dd[:, 4].sum()), "recent_volume": float(rec)}
                prev = g[max(0, d - 14):d, :kT, 4]
                vols = np.nansum(prev, axis=1)
                vols = vols[~np.isnan(g[max(0, d - 14):d, :kT, 3]).all(axis=1)]
                if len(vols) >= 7:
                    same[s] = float(vols.mean())
                    same_r[s] = float(np.nansum(g[max(0, d - 14):d, max(0, kT - win):kT, 4], axis=1).mean())
                # one-minute ATR20 for the scan's cost ratio; spread = stocks_spread_frac
                flat = g[max(0, d - 1):d + 1].reshape(-1, 5)
                flat = flat[: (NK if d > 0 else 0) + kT]
                flat = flat[~np.isnan(flat[:, 3])][-21:]
                if len(flat) >= 21:
                    pcl = flat[:-1, 3]; hh = flat[1:, 1]; ll = flat[1:, 2]
                    a1 = float(np.mean(np.maximum(hh - ll, np.maximum(abs(hh - pcl), abs(ll - pcl)))))
                    px = float(dd[-1, 3]); sp = px * float(getattr(L, "stocks_spread_frac", 0.0003))
                    cr[s] = scan_cost_ratio(px - sp / 2, px + sp / 2, a1, CAPITAL, L, CM)
            if not stats:
                continue
            r = rank(stats, live, T, L, same_time=same, same_time_recent=same_r, cost_ratio=cr)
            if r:
                out[(d, kT)] = [x.symbol for x in r[:int(L.stocks_top_k)]]
    return out


# ════════════════════════════════════════════════════════════════════════════
# 3. candidates: the engine's gates and exits on every marked bar
# ════════════════════════════════════════════════════════════════════════════
GATES = ["pass", "regime", "no_setup", "no_viable_stop", "risk_too_large", "cost_too_large_for_stop",
         "target_not_achievable", "move_too_small_for_costs", "no_exit"]
_TF = [1]


def _shim():
    """Higher-timeframe folds in evaluate/trailing are by wall clock with 60 s bars;
    on 3/5-minute bars they must fold by the bar's own length (research only - a
    live 5-minute desk needs the same change)."""
    import arb.confluence as cf, arb.trailing as trl, arb.smc as smc
    orig = smc.resample

    def rs(candles, factor, seconds=60, min_fill=1.0):
        return orig(candles, factor, seconds=60 * _TF[0], min_fill=min_fill)
    cf.resample = rs
    trl.resample = rs


def _rows_tf(g, days, tf):
    """Flattened bars on a tf-minute clock: rows [iso,o,h,l,c,v], (d, k_end) per row,
    and for every 1m flat bar the index of the tf row that ends at it (or -1)."""
    rows, pos = [], []
    for d in range(g.shape[0]):
        day = g[d]
        ok = ~np.isnan(day[:, 3])
        if not ok.any():
            continue
        for b in range(0, NK, tf):
            blk = day[b:b + tf]; m = ok[b:b + tf]
            if not m.any():
                continue
            x = blk[m]
            last = b + int(np.flatnonzero(m)[-1])
            rows.append([iso_of(days[d], b), float(x[0, 0]), float(x[:, 1].max()), float(x[:, 2].min()),
                         float(x[-1, 3]), float(x[:, 4].sum())])
            pos.append((d, b, last))
    return rows, pos


class Stock:
    def __init__(self, s, days):
        g = np.load(f"{GRID}/{s}.npy")
        self.g = g
        self.name = s
        self.rows = {}
        self.pos = {}
        self.at = {}
        for tf in TFS:
            r, p = _rows_tf(g, days, tf)
            self.rows[tf], self.pos[tf] = r, p
            # (d, k of the last 1m bar in the bucket) -> row index
            self.at[tf] = {(d, last): i for i, (d, b, last) in enumerate(p)}


def _size(px, risk, L, cost):
    cap = CAPITAL * L.max_risk_pct_per_trade / 100.0
    lev = float(getattr(L, "stocks_leverage", 5.0))
    q = min(int(cap // risk), int(CAPITAL * lev // px))
    lo, hi = 0, max(0, q)
    while lo < hi:                                   # risk_includes_costs (IntradayEngine._stock_qty)
        mid = (lo + hi + 1) // 2
        if risk * mid + cost(px, mid) <= cap:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _simulate(st, tf, i, side, px, risk, inv, tgt, qty, rt, L):
    """Exits on 1-minute prices (stop/target as ticks would see them), trailing and
    extensions on completed tf bars. Returns (exit, why, minutes held) or None."""
    from arb.positions import Position
    from arb import trailing
    rows, pos = st.rows[tf], st.pos[tf]
    d, b, last = pos[i]
    g = st.g[d]
    p = Position(trade_id=0, symbol=st.name, token="", strike=0, opt="EQ", side=side, qty=qty,
                 entry=100.0, stop=0.0, target=1e9, risk_points=risk, index_invalidation=inv,
                 opened_at=0, cost_points=rt / qty)
    p.index_entry, p.index_risk, p.index_target, p.delta, p.entry_bar = px, risk, tgt, 1.0, rows[i][0]
    ent = last
    j_tf = i
    prev_c = px
    for k in range(ent + 1, NK):
        if np.isnan(g[k, 3]):
            continue
        o, h, l, c = (float(x) for x in g[k, :4])
        if k > K_SQ:
            return prev_c, "squareoff", k - 1 - ent
        stop = p.index_invalidation
        if (side > 0 and l <= stop) or (side < 0 and h >= stop):
            ex = min(o, stop) if side > 0 else max(o, stop)
            return ex, ("trail_stop" if p.index_trailed else "stop"), k - ent
        if p.index_target and ((side > 0 and h >= p.index_target) or (side < 0 and l <= p.index_target)):
            done = rows[max(0, j_tf - 399):j_tf + 1]
            if not trailing.try_extend(p, done + [done[-1]], p.index_target, L, 0.0):
                return p.index_target, "target", k - ent
        if k - ent >= L.time_stop_min and (c - px) * side / risk < L.time_stop_min_r:
            return c, "time_stop", k - ent
        prev_c = c
        nxt = st.at[tf].get((d, k))
        if nxt is not None:
            j_tf = nxt
            w = rows[max(0, j_tf - 199):j_tf + 1]
            trailing.update(p, w + [w[-1]], L, index_px=c)
        if k == K_SQ:
            return c, "squareoff", k - ent
    return None


def work(args):
    """Every marked bar through the engine's decision layer up to the stop; every
    bar with a viable structural stop becomes a SIGNAL, simulated twice on the
    underlying: with the engine's momentum target (only when it is >= min_target_r,
    as the engine requires) and with a fixed 2R target (target_mode='fixed').
    Sizing, the cost gate, the net gate and the P&L (cash or option) are applied
    in the report, so one pass serves every execution variant."""
    s, si, todo = args
    from arb.confluence import evaluate
    from arb.candlesticks import engulfing, rejection
    from arb.intraday import structural_invalidation
    from arb.regime import atr, atr_ratio, session_phase
    from arb.smc import analyse, RANGE
    from arb import trailing
    import stock_backtest as sb
    L = sb.L
    cost = sb.cost
    _shim()
    days = todo["days"]
    st = Stock(s, days)
    F, sig = [], []
    kinds = ["smc1", "smc3", "smc5", "mom_spike", "mom_swing1", "mom_swing5"]

    def signal(kind, tf, i, seg, d, last, side, grade, px, inv, risk, a, ar, mid):
        q = _size(px, risk, L, cost)
        cps = cost(px, max(q, 1)) / max(q, 1)
        tgt = trailing.initial_target(seg, side, px, risk, L, 0.0)
        reach = abs(tgt - px) if tgt else L.reward_risk * risk
        rec = dict(s=si, d=d, k=last, tf=tf, kind=kinds.index(kind), ar=ar, mid=mid, side=side, grade=grade,
                   px=px, inv=inv, risk=risk, atr=a, reach=reach, M=None, F=None)
        if reach >= float(getattr(L, "min_target_r", 1.0)) * risk:
            rec["M"] = _simulate(st, tf, i, side, px, risk, inv, tgt, 1, cps, L)
        rec["F"] = _simulate(st, tf, i, side, px, risk, inv, px + side * float(L.reward_risk) * risk, 1, cps, L)
        sig.append(rec)

    for tf in TFS:
        _TF[0] = tf
        rows, pos = st.rows[tf], st.pos[tf]
        for i, (d, b, last) in enumerate(pos):
            if not todo["smc"][d, last] or i < L.min_structure_bars:
                continue
            seg = rows[max(0, i - 399):i + 1]
            km = K0 + b + tf                         # decision minute (the bar has closed)
            now = datetime(2026, 1, 1, km // 60, km % 60)
            ar = atr_ratio(seg, period=L.atr_period, baseline_bars=L.atr_baseline_bars)
            mid = session_phase(now=now) == "midday"
            px = seg[-1][4]
            base = [si, d, last, kinds.index(f"smc{tf}")]
            if ar < (1.5 if mid else 1.3):
                # the engine's regime gate comes first (pace 1.3, midday 1.5); exact
                # for every variant reported, and it halves the evaluate() calls
                F.append(base + [1, 0, mid, ar, np.nan]); continue
            if engulfing(seg) == RANGE and rejection(seg) == RANGE:
                F.append(base + [2, 0, mid, ar, np.nan]); continue    # ob_fvg_retest needs a reaction bar
            v = evaluate(seg, px, L, strike_step=0.0)
            if not v.tradable:
                F.append(base + [2, 0, mid, ar, np.nan]); continue
            stc = analyse(seg, strength=L.swing_strength, min_bars=L.min_structure_bars)
            lead = v.setups[0] if v.setups else None
            inv = structural_invalidation(stc, v.side, lead.invalidation if lead else None)
            ref = stc.closes[-1] if stc.closes else px
            risk = abs(ref - inv) if inv else 0.0
            a = atr(seg[:-1], period=L.atr_period)
            if inv and ((v.side > 0 and inv >= px) or (v.side < 0 and inv <= px)):
                # structural_invalidation fell back to a swing price has already
                # crossed: the engine's abs() would size it and be stopped at once.
                F.append(base + [9, v.side, mid, ar, 100 * risk / px]); continue
            if risk <= 0 or (a > 0 and risk < L.min_stop_atr * a):
                F.append(base + [3, v.side, mid, ar, 100 * risk / px]); continue
            F.append(base + [0, v.side, mid, ar, 100 * risk / px])
            signal(f"smc{tf}", tf, i, seg, d, last, v.side, v.grade, px, inv, risk, a, ar, mid)
    # momentum: the bar a spike threshold is crossed, on 1-minute bars
    _TF[0] = 1
    rows, pos = st.rows[1], st.pos[1]
    rows5, pos5 = st.rows[5], st.pos[5]
    idx5 = {(d, last): j for j, (d, b, last) in enumerate(pos5)}
    for i, (d, b, last) in enumerate(pos):
        side = int(todo["mom"][d, last])
        if side == 0 or i < 25:
            continue
        seg = rows[max(0, i - 399):i + 1]
        px = seg[-1][4]
        km = K0 + last + 1
        now = datetime(2026, 1, 1, km // 60, km % 60)
        ar = atr_ratio(seg, period=L.atr_period, baseline_bars=L.atr_baseline_bars)
        mid = session_phase(now=now) == "midday"
        a = atr(seg[:-1], period=L.atr_period)
        if ar < (1.5 if mid else 1.3):
            for kind in ("mom_spike", "mom_swing1", "mom_swing5"):
                F.append([si, d, last, kinds.index(kind), 1, side, mid, ar, np.nan])
            continue
        lo5 = min(x[3] for x in seg[-5:]); hi5 = max(x[2] for x in seg[-5:])
        stops = {"mom_spike": (lo5 - 0.1 * a) if side > 0 else (hi5 + 0.1 * a)}
        stc = analyse(seg, strength=L.swing_strength, min_bars=L.min_structure_bars)
        stops["mom_swing1"] = structural_invalidation(stc, side, None)
        j5 = None
        for back in range(0, 5):                     # the last COMPLETED 5-minute bar
            j5 = idx5.get((d, last - back))
            if j5 is not None:
                break
        if j5 is not None and j5 >= 25:
            _TF[0] = 5
            st5 = analyse(rows5[max(0, j5 - 399):j5 + 1], strength=L.swing_strength, min_bars=L.min_structure_bars)
            _TF[0] = 1
            stops["mom_swing5"] = st5.invalidation(side) or 0.0
        for kind, inv in stops.items():
            base = [si, d, last, kinds.index(kind)]
            if not inv or (side > 0 and inv >= px) or (side < 0 and inv <= px):
                F.append(base + [3, side, mid, ar, np.nan]); continue
            risk = abs(px - inv)
            if a > 0 and risk < L.min_stop_atr * a:
                F.append(base + [3, side, mid, ar, 100 * risk / px]); continue
            F.append(base + [0, side, mid, ar, 100 * risk / px])
            signal(kind, 1, i, seg, d, last, side, "B", px, inv, risk, a, ar, mid)
    np.save(f"{OUT}/cands/{s}_funnel.npy", np.array(F, dtype=np.float64).reshape(-1, 9))
    pickle.dump(sig, open(f"{OUT}/cands/{s}_trades.pkl", "wb"))
    return s, len(F), len(sig)



def cands(procs=3, only=None):
    names, days, *_ = load_meta()
    z = np.load(f"{OUT}/features.npz")
    MARK, MOM, DIR = z["MARK"], z["MOM"], z["DIR"]
    os.makedirs(f"{OUT}/cands", exist_ok=True)
    jobs = []
    for si, s in enumerate(names):
        if only and s not in only:
            continue
        if os.path.exists(f"{OUT}/cands/{s}_trades.pkl") and not only:
            continue
        # compact per-stock arrays: a set of 25k tuples per stock in the parent was
        # ~1 GB shared into every forked worker (6 Oct 11:40)
        jobs.append((s, si, {"days": days, "smc": MARK[si].copy(), "mom": np.where(MOM[si], DIR[si], 0).astype(np.int8)}))
    del MARK, MOM, DIR, z
    import gc; gc.collect(); gc.freeze()
    t0 = time.time()
    with Pool(procs, maxtasksperchild=8) as pool:
        for n, (s, nr, nt) in enumerate(pool.imap_unordered(work, jobs), 1):
            print(f"[{n}/{len(jobs)}] {s}: {nr} bars evaluated, {nt} signals  ({time.time() - t0:.0f}s)",
                  flush=True)


# ════════════════════════════════════════════════════════════════════════════
# 4. report: every variant through the book
# ════════════════════════════════════════════════════════════════════════════
RISK_PCT = 6.0                 # stock trades: same as the indices (owner, 6 Oct, ceiling 7%); costs inside
KINDS = ["smc1", "smc3", "smc5", "mom_spike", "mom_swing1", "mom_swing5"]
WHY = ["stop", "trail_stop", "target", "time_stop", "squareoff"]
COLS = ["s", "d", "k", "tf", "kind", "ar", "mid", "side", "grade", "px", "inv", "risk", "atr", "reach",
        "M_ex", "M_why", "M_held", "F_ex", "F_why", "F_held"]
C = {c: i for i, c in enumerate(COLS)}


def load_signals(names):
    """Every stock's signals as one float array (COLS), and the funnel rows."""
    blocks, funnel = [], []
    for s in names:
        p = f"{OUT}/cands/{s}_trades.pkl"
        if not os.path.exists(p):
            continue
        sig = pickle.load(open(p, "rb"))
        a = np.full((len(sig), len(COLS)), np.nan)
        for n, r in enumerate(sig):
            a[n, :14] = (r["s"], r["d"], r["k"], r["tf"], r["kind"], r["ar"], r["mid"], r["side"],
                         "ABC".index(r["grade"]) if r["grade"] in "ABC" else 1, r["px"], r["inv"], r["risk"],
                         r["atr"], r["reach"])
            for j, m in ((14, "M"), (17, "F")):
                if r[m] is not None:
                    ex, why, held = r[m]
                    a[n, j:j + 3] = (ex, WHY.index(why), held)
        blocks.append(a)
        funnel.append(np.load(f"{OUT}/cands/{s}_funnel.npy"))
    return np.concatenate(blocks), np.concatenate(funnel)


def cash_outcomes(A, capital=CAPITAL, risk_pct=None):
    """The engine's stock-entry gates after the stop, and the rupee result, per signal
    and target mode. gate: 0 ok, 4 risk_too_large, 5 cost, 6 target, 7 net, 8 no exit."""
    import stock_backtest as sb
    L, cost = sb.L, sb.cost
    n = len(A)
    out = {m: dict(gate=np.zeros(n, np.int8), net=np.zeros(n), R=np.zeros(n), held=np.zeros(n, np.int32),
                   cost=np.zeros(n), qty=np.zeros(n, np.int32)) for m in ("M", "F")}
    cap = capital * (risk_pct or RISK_PCT) / 100.0
    lev = float(getattr(L, "stocks_leverage", 5.0))
    frac = float(L.max_cost_frac_of_risk); min_net = float(getattr(L, "stocks_min_net_inr", 100.0))
    for i in range(n):
        px, risk, side = A[i, C["px"]], A[i, C["risk"]], A[i, C["side"]]
        q = min(int(cap // risk), int(capital * lev // px))
        lo, hi = 0, max(0, q)
        while lo < hi:
            mid = (lo + hi + 1) // 2
            if risk * mid + cost(px, mid) <= cap:
                lo = mid
            else:
                hi = mid - 1
        q = lo
        rt = cost(px, q) if q > 0 else 0.0
        for m in ("M", "F"):
            o = out[m]
            o["qty"][i], o["cost"][i] = q, rt
            if q <= 0:
                o["gate"][i] = 4; continue
            if rt > frac * risk * q:
                o["gate"][i] = 5; continue
            reach = A[i, C["reach"]] if m == "M" else float(L.reward_risk) * risk
            if m == "M" and reach < float(getattr(L, "min_target_r", 1.0)) * risk:
                o["gate"][i] = 6; continue
            if reach * q - rt < min_net:
                o["gate"][i] = 7; continue
            ex = A[i, C[f"{m}_ex"]]
            if np.isnan(ex):
                o["gate"][i] = 8; continue
            o["net"][i] = (ex - px) * side * q - rt
            o["R"][i] = o["net"][i] / (risk * q)
            o["held"][i] = int(A[i, C[f"{m}_held"]])
    return out


def book_idx(idx, A, net, held, prio, capital=CAPITAL, halt_frac=0.15, max_trades=15):
    """One position at a time across every stock, with the engine's discipline:
    15 a day, 15% daily halt, 180 s after a loss, 300 s per symbol, 3 losses ->
    30 min pause and grade B or better until a win. Returns the booked indices."""
    if len(idx) == 0:
        return idx
    d = A[idx, C["d"]]; m = A[idx, C["k"]] + K0 + 1
    order = np.lexsort((A[idx, C["s"]], -prio[idx], m, d))
    idx = idx[order]
    out = []
    cur = None
    for i in idx:
        day = A[i, C["d"]]
        if day != cur:
            cur, busy, n, pnl, streak, pause, last_loss, careful, sym_free = day, -1, 0, 0.0, 0, -1, -10 ** 9, False, {}
        mm = A[i, C["k"]] + K0 + 1
        s = A[i, C["s"]]
        if mm <= busy or mm < pause or mm - last_loss < 3:
            continue
        if n >= max_trades or pnl <= -halt_frac * capital:
            continue
        if mm < sym_free.get(s, -1):
            continue
        if careful and A[i, C["grade"]] > 1:
            continue
        out.append(i); n += 1; pnl += net[i]
        busy = mm + held[i]
        sym_free[s] = busy + 5
        if net[i] < 0:
            streak += 1; last_loss = busy
            if streak >= 3:
                pause, streak, careful = busy + 30, 0, True
        else:
            streak, careful = 0, False
    return np.array(out, dtype=np.int64)


def summary(b, A, net, R, days, ndays):
    if len(b) == 0:
        return dict(n=0, win=0, IN=0, OUT=0, tot=0, dd=0, R=0, tpd=0, pdays=0, nin=0, nout=0)
    dd_ = A[b, C["d"]].astype(int)
    split_d = next(i for i, d in enumerate(days) if d >= SPLIT)
    ins = dd_ < split_d
    eq = np.cumsum(net[b])
    dd = float(np.max(np.maximum.accumulate(np.r_[0, eq]) - np.r_[0, eq]))
    return dict(n=len(b), win=100 * np.mean(net[b] > 0), IN=net[b][ins].sum(), OUT=net[b][~ins].sum(),
                tot=net[b].sum(), dd=dd, R=R[b].mean(), tpd=len(b) / ndays, pdays=100 * len(set(dd_)) / ndays,
                nin=int(ins.sum()), nout=int((~ins).sum()))


def fmt(sm):
    return (f"{sm['n']:4d} {sm['win']:4.0f}% {sm['IN']:8,.0f} {sm['OUT']:8,.0f} {sm['tot']:8,.0f} "
            f"{sm['dd']:7,.0f} {sm['R']:+5.2f} {sm['tpd']:5.2f} {sm['pdays']:4.0f}%")


HDR = "   n  win       IN      OUT    total   maxDD  avgR  t/day days%"


# ── options on the same signals ─────────────────────────────────────────────
def option_setup(names, days):
    """Lot, strike step (scrip master, OPTSTK), realised vol (20 daily closes before
    the day) x 1.1, and the monthly expiry (last Tuesday; on expiry week the next)."""
    from datetime import date, timedelta
    meta = json.load(open(f"{OUT}/optstk.json"))
    S, D = len(names), len(days)
    lot = np.array([meta[s]["lot"] for s in names], float)
    step = np.array([meta[s]["step"] for s in names], float)
    sig = np.full((S, D), np.nan)
    for si, s in enumerate(names):
        daily = json.load(open(f"{H}/{s}_1d.json"))
        for di, d in enumerate(days):
            c = np.array([r[4] for r in daily if r[0] < d][-21:], float)
            if len(c) >= 15:
                sig[si, di] = np.std(np.diff(np.log(c)), ddof=1) * np.sqrt(252) * 1.1
    tdays = [date.fromisoformat(d) for d in days]
    tset = set(tdays)

    def last_tue(y, m):
        nxt = date(y + (m == 12), m % 12 + 1, 1)
        x = nxt - timedelta(days=1)
        while x.weekday() != 1:
            x -= timedelta(days=1)
        while x not in tset and x <= tdays[-1] and x >= tdays[0]:
            x -= timedelta(days=1)
        return x
    exp = []
    for d in tdays:
        e = last_tue(d.year, d.month)
        if e < d or (e - d).days < 7 - d.weekday() + 1 and (e - d).days <= 6:
            y, m = (d.year + (d.month == 12), d.month % 12 + 1)
            e = last_tue(y, m)
        exp.append(e)
    return lot, step, sig, tdays, exp


def option_outcomes(A, idx, mode, opt, capital, max_otm=3, theta_f=0.55, risk_pct=None):
    """Buy CE for a long signal, PE for a short; nearest-ATM strike (ATM .. max_otm
    OTM) whose lot fits the capital and whose stop-out (BS reprice at the stop,
    spread, exact fees) fits risk_pct of capital. Exits where the cash signal exits
    (the engine's index-style rule on the underlying). Returns net, gate, mincap.
    gate: 0 ok, 1 no exit, 2 unaffordable, 3 risk cap, 4 cost > 20% of risk."""
    sys.path.insert(0, os.path.join(ROOT, "research", "lab"))
    from bsmodel import bs, T_years
    from arb.costs import leg_cost
    from arb.config import CostModel
    CM = CostModel.load()
    lot, step, sigd, tdays, exp = opt
    rp = (risk_pct or RISK_PCT) / 100.0
    cap = capital * rp
    net = np.zeros(len(A)); gate = np.full(len(A), -1, np.int8); mincap = np.full(len(A), np.nan)
    info = {}
    for i in idx:
        s, d, k = int(A[i, C["s"]]), int(A[i, C["d"]]), int(A[i, C["k"]])
        side, S0, inv = int(A[i, C["side"]]), A[i, C["px"]], A[i, C["inv"]]
        sig = sigd[s, d]
        if np.isnan(sig):
            gate[i] = 1; continue
        t0 = datetime(tdays[d].year, tdays[d].month, tdays[d].day, 9, 15) + __import__("datetime").timedelta(minutes=k + 1)
        T0 = T_years(t0, exp[d], tdays)
        o = "CE" if side > 0 else "PE"
        st, L_ = step[s], lot[s]
        atm = round(S0 / st) * st
        best, need = None, np.inf
        for off in range(0, max_otm + 1):
            K = atm + side * off * st
            mid = bs(S0, K, T0, sig, o)
            sp = max(0.005 * mid, 0.05)
            ask = mid + sp
            ms = bs(inv, K, T0, sig, o)
            bid_s = max(0.05, ms - max(0.005 * ms, 0.05))
            fees1 = leg_cost(ask, L_, "BUY", "OPT", CM) + leg_cost(bid_s, L_, "SELL", "OPT", CM)
            loss1 = (ask - bid_s) * L_ + fees1
            need = min(need, max(ask * L_, loss1 / rp))
            if best is None:
                if ask * L_ > capital:
                    continue
                if loss1 > cap:
                    continue
                best = (K, ask, sp, loss1, fees1)
        mincap[i] = need
        if best is None:
            # which constraint bound: any strike affordable at all?
            afford = any(bs(S0, atm + side * off * st, T0, sig, o) * 1.005 * L_ <= capital for off in range(max_otm + 1))
            gate[i] = 3 if afford else 2
            continue
        K, ask, sp, loss1, fees1 = best
        lots = max(1, min(int(capital // (ask * L_)), int(cap // loss1)))
        q = lots * L_
        if fees1 > 0.20 * loss1:
            gate[i] = 4; continue
        ex, held = A[i, C[f"{mode}_ex"]], A[i, C[f"{mode}_held"]]
        if np.isnan(ex):
            gate[i] = 1; continue
        t1 = t0 + __import__("datetime").timedelta(minutes=int(held))
        T1 = T0 - theta_f * (T0 - T_years(t1, exp[d], tdays))
        m1 = bs(ex, K, max(T1, 0.0), sig, o)
        bid = max(0.05, m1 - max(0.005 * m1, 0.05))
        fees = leg_cost(ask, q, "BUY", "OPT", CM) + leg_cost(bid, q, "SELL", "OPT", CM)
        net[i] = (bid - ask) * q - fees
        gate[i] = 0
        info[i] = (K, ask, lots, q, ask * q, loss1 * lots)
    return net, gate, mincap, info


def report():
    import warnings; warnings.filterwarnings("ignore")
    names, days, *_ = load_meta()
    z = np.load(f"{OUT}/features.npz")
    A5n, R15n, RVn = np.nan_to_num(z["A5"]), np.nan_to_num(z["R15"]), np.nan_to_num(z["RV"])
    RANK, LIQ, ev, ctrl, DESK = z["RANK"], z["LIQ"], z["ev"], z["ctrl"], z["DESK"]
    del z
    S, D = len(names), len(days)
    t0 = time.time()
    A, F = load_signals(names)
    first_day = 5
    keep = A[:, C["d"]] >= first_day
    A = A[keep]; F = F[F[:, 1] >= first_day]
    ndays = D - first_day
    split_d = next(i for i, d in enumerate(days) if d >= SPLIT)
    print(f"{len(A):,} signals, {len(F):,} evaluated bars ({time.time() - t0:.0f}s)", flush=True)
    CO = cash_outcomes(A)
    print(f"cash outcomes ({time.time() - t0:.0f}s)", flush=True)
    si, di, ki = A[:, C["s"]].astype(int), A[:, C["d"]].astype(int), A[:, C["k"]].astype(int)
    kind = A[:, C["kind"]].astype(int)
    reg = np.where(A[:, C["mid"]] > 0, A[:, C["ar"]] >= 1.5, A[:, C["ar"]] >= 1.3)
    prio = A5n[si, di, ki]
    kk = np.arange(NK); tk = (kk >= K_FIRST) & (kk <= K_LAST)
    lines = []
    P = lambda *a: lines.append(" ".join(str(x) for x in a))

    def spike_state(metric, th, nu, N):
        x = (A5n if metric == "A" else R15n) >= th
        if nu > 0:
            x &= RVn >= nu
        if N:
            x &= RANK <= N
        return x & LIQ & tk[None, None, :]

    def spike_cross(metric, th, nu, N):
        x = crossed(A5n if metric == "A" else R15n, th)
        if nu > 0:
            x &= RVn >= nu
        if N:
            x &= RANK <= N
        return x & LIQ & tk[None, None, :]

    def dilate(mask, W):
        out = mask.copy()
        for w in range(1, W + 1):
            out[:, :, w:] |= mask[:, :, :-w]
        return out

    def ctrl_window(mask, W):
        sel = mask[ev[:, 0], ev[:, 1], ev[:, 2]] & (ctrl >= 0)
        e, c = ev[sel], ctrl[sel]
        out = np.zeros_like(mask)
        for w in range(W + 1):
            out[c, e[:, 1], np.minimum(e[:, 2] + w, NK - 1)] = True
        return out

    def run(allow, kinds_, mode, regime=True, capital=CAPITAL, net=None, okm=None, co=None, rp=None):
        o = (co or CO)[mode]
        ok = np.isin(kind, kinds_) & allow[si, di, ki]
        if regime:
            ok &= reg
        cand = np.flatnonzero(ok)
        good = cand[(o["gate"][cand] == 0) if okm is None else okm[cand]]
        nn = o["net"] if net is None else net
        R = o["R"] if net is None else nn / (capital * (rp or RISK_PCT) / 100.0)
        b = book_idx(good, A, nn, o["held"], prio, capital=capital)
        return summary(b, A, nn, R, days, ndays), b, cand

    def allow_of(lab):
        if lab[4].startswith("smc"):
            return dilate(spike_state(*lab[:4]), lab[5])
        return spike_cross(*lab[:4])

    grid = [(m_, th, nu, N) for m_ in ("A", "B") for th in (2.0, 2.5, 3.0) for nu in (0, 1.5, 2.0, 3.0)
            for N in (0, 20, 10)]
    results = {}
    for g in grid:
        st_ = spike_state(*g); cr_ = spike_cross(*g)
        for kd in KINDS:
            for W in ((30, 60) if kd.startswith("smc") else (0,)):
                allow = dilate(st_, W) if W else cr_
                callow = ctrl_window(st_, W) if W else ctrl_window(cr_, 0)
                for mode in ("M", "F"):
                    lab = g + (kd, W, mode)
                    sm, b, cand = run(allow, [KINDS.index(kd)], mode)
                    cm, cb, _ = run(callow, [KINDS.index(kd)], mode)
                    results[lab] = (sm, b, cm, cb)
        print(f"grid {g} ({time.time() - t0:.0f}s)", flush=True)
    base_rows = {}
    allliq = LIQ & tk[None, None, :]
    for mode in ("M", "F"):
        for kd in ("smc1", "smc3", "smc5"):
            kx = [KINDS.index(kd)]
            base_rows[f"desk top-5/30min (live), {kd}, {mode}"] = run(DESK, kx, mode)[0]
            base_rows[f"every liquid stock, no spike, {kd}, {mode}"] = run(allliq, kx, mode)[0]
            for N in (20, 10):
                base_rows[f"top-{N} movers, no spike, {kd}, {mode}"] = run((RANK <= N) & allliq, kx, mode)[0]

    def works(lab):
        sm = results[lab][0]
        return sm["n"] >= 30 and sm["IN"] > 0 and sm["OUT"] > 0

    def neighbours(lab):
        out = []
        for pos, vals in ((1, (2.0, 2.5, 3.0)), (2, (0, 1.5, 2.0, 3.0)), (3, (0, 20, 10))):
            i = vals.index(lab[pos])
            for j in (i - 1, i + 1):
                if 0 <= j < len(vals):
                    x = list(lab); x[pos] = vals[j]; out.append(tuple(x))
        return out

    def plateau(lab):
        return works(lab) and all(results[n][0]["tot"] > 0 for n in neighbours(lab))

    def beats_ctrl(lab):
        return results[lab][0]["tot"] > results[lab][2]["tot"]

    P(f"Stock spike desk - {S} F&O stocks, {D} sessions {days[0]} .. {days[-1]}. First {first_day} sessions are "
      f"history only; IN {split_d - first_day} sessions to {SPLIT}, OUT {D - split_d} sessions.")
    P(f"Cash MIS: capital Rs {CAPITAL:,.2f}, 5x, {RISK_PCT:g}% risk cap with costs inside, cost <= 20% of risk, target >= 1R, "
      f"net at target >= Rs 100; one position; 15% daily halt; entries 09:30-14:59; square-off 15:14.")
    P("Regime gate (pace 1.3, midday 1.5) on the entry timeframe, always (applied before evaluate, as the engine does).")
    P("metric A = ATR5 / median TR of the 200 bars before; B = 15-min range / the same 15 min on prior sessions.")
    P("rv = 5-min volume / same 5 min on prior sessions (0 = off). N = top-N |move from prev close| (0 = all liquid).")
    P("entry: smcT = ob_fvg_retest on T-minute bars within W min of a spike; mom_* = at the bar a threshold is crossed.")
    P("tgt: M = the engine's momentum target (needs >= 1R); F = fixed 2R target (target_mode 'fixed').")
    P("ctrl = same entry, same minutes, on a random liquid stock that was not spiking (A < 1.5 and B < 1.5).")
    P("")
    P("== Baselines without a spike filter ==")
    P(f"{'':52s}{HDR}")
    for k_, sm in base_rows.items():
        P(f"{k_:52s}{fmt(sm)}")
    P("")
    P("== Every spike variant ==  (* works: n>=30, IN>0, OUT>0;  P: also every grid neighbour (th, rv, N) positive)")
    P(f"{'met th   rv   N entry      W tgt':36s}{HDR}  | control {HDR}")
    for lab in sorted(results, key=lambda l: (l[4], l[5], l[6], l[0], l[1], l[2], l[3])):
        sm, b, cm, cb = results[lab]
        tag = "P" if plateau(lab) else ("*" if works(lab) else " ")
        P(f"{lab[0]:>3s} {lab[1]:3.1f} {lab[2]:3.1f} {lab[3]:3d} {lab[4]:10s} {lab[5]:2d} {lab[6]} {tag}{fmt(sm)}  | {fmt(cm)}")
    P("")
    n_w = sum(works(l) for l in results); plats = [l for l in results if plateau(l)]
    P(f"variants that work: {n_w}/{len(results)}; on a plateau: {len(plats)}; "
      f"plateau AND beating its control: {sum(beats_ctrl(l) for l in plats)}")
    P("")
    P("== Best per entry and target mode (by total, n >= 30) ==")
    best_by = {}
    for kd in KINDS:
        for mode in ("M", "F"):
            labs = [l for l in results if l[4] == kd and l[6] == mode and results[l][0]["n"] >= 30]
            nall = sum(1 for l in results if l[4] == kd and l[6] == mode)
            if not labs:
                P(f"{kd:10s} {mode}: no variant with n >= 30 (of {nall})"); continue
            lab = max(labs, key=lambda l: results[l][0]["tot"])
            best_by[(kd, mode)] = lab
            P(f"{kd:10s} {mode}: {str(lab[:4] + (lab[5],)):26s} {fmt(results[lab][0])}  ctrl {results[lab][2]['tot']:8,.0f}"
              f" | work {sum(works(l) for l in results if l[4] == kd and l[6] == mode)}/{nall},"
              f" plateau {sum(plateau(l) for l in results if l[4] == kd and l[6] == mode)}")
    cands_ = [l for l in plats if beats_ctrl(l)]
    if cands_:
        pick, status = max(cands_, key=lambda l: results[l][0]["tot"]), "plateau, beats its control"
    elif plats:
        pick, status = max(plats, key=lambda l: results[l][0]["tot"]), "plateau, but does NOT beat its control"
    elif n_w:
        pick, status = max([l for l in results if works(l)], key=lambda l: results[l][0]["tot"]), "works but no plateau"
    else:
        pick, status = max(results, key=lambda l: results[l][0]["tot"]), "does not work"
    # ── funnel ──
    P("")
    P("== Gate funnel (first failing gate; evaluated bars inside the variant's spike windows) ==")
    gname = {2: "no_setup", 3: "no_viable_stop", 9: "stop_on_wrong_side_of_price", 4: "risk_too_large",
             5: "cost_too_large_for_stop", 6: "target_not_achievable", 7: "move_too_small_for_costs", 8: "no_exit"}
    show = [pick] + [best_by[k_] for k_ in sorted(best_by)]
    seen = set()
    for lab in show:
        if lab in seen:
            continue
        seen.add(lab)
        allow = allow_of(lab)
        kd = KINDS.index(lab[4])
        f = F[F[:, 3] == kd]
        f = f[allow[f[:, 0].astype(int), f[:, 1].astype(int), f[:, 2].astype(int)]]
        regf = np.where(f[:, 6] > 0, f[:, 7] >= 1.5, f[:, 7] >= 1.3)
        tot = max(1, len(f))
        cnt = {"regime_too_quiet/midday_chop": int((~regf).sum())}
        g2 = f[regf]
        for gc in (2, 9, 3):
            cnt[gname[gc]] = int((g2[:, 4] == gc).sum())
        ok = np.isin(kind, [kd]) & allow[si, di, ki] & reg
        gg = CO[lab[6]]["gate"][ok]
        for gc in (4, 5, 6, 7, 8):
            cnt[gname[gc]] = int((gg == gc).sum())
        cnt["passed every gate"] = int((gg == 0).sum())
        P(f"{lab}: {len(f):,} bar evaluations")
        for k_, v in cnt.items():
            P(f"   {k_:32s} {v:9,d}  ({100 * v / tot:5.2f}%)")
        sp = A[ok, C["risk"]] / A[ok, C["px"]] * 100
        cq = CO[lab[6]]
        cf = cq["cost"][ok] / np.maximum(1e-9, A[ok, C["risk"]] * cq["qty"][ok])
        if ok.sum():
            P(f"   signals with a viable stop: stop median {np.median(sp):.2f}% of price (p25 {np.percentile(sp, 25):.2f}, "
              f"p75 {np.percentile(sp, 75):.2f}); round trip / risk median {100 * np.median(cf[cq['qty'][ok] > 0]):.0f}%; "
              f"target reach median {np.median(A[ok, C['reach']] / A[ok, C['risk']]):.2f}R")
    # ── the pick ──
    P("")
    sm, b, cm, cb = results[pick]
    P(f"== Best variant: {pick}  [{status}] ==")
    P(f"   spike  : {fmt(sm)}")
    P(f"   control: {fmt(cm)}")
    P(f"   neighbours: " + "; ".join(f"{n[1:4]} {results[n][0]['tot']:,.0f}" for n in neighbours(pick)))
    o = CO[pick[6]]
    mon = {}
    for i in b:
        mon.setdefault(days[int(A[i, C["d"]])][:7], []).append(o["net"][i])
    for k_ in sorted(mon):
        v = mon[k_]
        P(f"   {k_}  n={len(v):3d}  win {100 * np.mean([x > 0 for x in v]):3.0f}%  net Rs {sum(v):8,.0f}")
    if len(b):
        why = {}
        for i in b:
            w = WHY[int(A[i, C[f'{pick[6]}_why']])]; why[w] = why.get(w, 0) + 1
        P(f"   exits: {why}")
        P(f"   mean stop {np.mean(A[b, C['risk']] / A[b, C['px']] * 100):.2f}% of price; mean cost Rs {o['cost'][b].mean():.0f}"
          f" = {100 * np.mean(o['cost'][b] / (A[b, C['risk']] * o['qty'][b])):.0f}% of risk; mean notional Rs "
          f"{np.mean(A[b, C['px']] * o['qty'][b]):,.0f}; mean hold {o['held'][b].mean():.0f} min")
        P("   trades (last 15):")
        for i in b[-15:]:
            m_ = int(A[i, C["k"]]) + K0 + 1
            P(f"     {days[int(A[i, C['d']])]} {m_ // 60:02d}:{m_ % 60:02d} {names[int(A[i, C['s']])]:12s} "
              f"{'L' if A[i, C['side']] > 0 else 'S'} {WHY[int(A[i, C[pick[6] + '_why']])]:10s} "
              f"net Rs {o['net'][i]:7,.0f}  R {o['R'][i]:+.2f}")
    P("")
    P(f"== Risk cap per stock trade, 1-10% of capital Rs {CAPITAL:,.2f} (cash MIS, costs inside, sized directly - not clamped at 7%) ==")
    def fmt2(sm, b, o):
        w = o["net"][b].min() if len(b) else 0.0
        ar_ = (A[b, C["risk"]] * o["qty"][b] + o["cost"][b]).mean() if len(b) else 0.0
        return f"{fmt(sm)}  worst {w:7,.0f}  avg risk Rs {ar_:5,.0f}"
    RSW = {}
    sweep = [("desk top-5/30min (live)", None, "smc1", "M")] + \
        [(str(lab), lab, lab[4], lab[6]) for lab in [pick] + [best_by[k_] for k_ in sorted(best_by) if best_by[k_] != pick]]
    for rp in (1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0, 9.0, 10.0):
        co = CO if rp == RISK_PCT else cash_outcomes(A, risk_pct=rp)
        for name, lab, kd, mode in sweep:
            allow = DESK if lab is None else allow_of(lab)
            smr, br, _ = run(allow, [KINDS.index(kd)], mode, co=co)
            RSW.setdefault(name, []).append(f"   risk {rp:4.0f}%: {fmt2(smr, br, co[mode])}")
        del co
    for name, rows_ in RSW.items():
        P(f"-- {name}")
        P(f"   {'':10s}{HDR}")
        for r_ in rows_:
            P(r_)
    # ── options on the same signals ──
    P("")
    P("== Option execution on the same signals (buy CE for a long, PE for a short; monthly expiry) ==")
    P("   BS with IV = 20-day realised vol x 1.1, spread max(0.5% of premium, 0.05) each side, exact OPT fees,")
    P("   time at 0.55 speed intraday; strike ATM..3 OTM, nearest ATM whose lot fits the capital and whose")
    P("   stop-out (BS at the stop + spread + fees) fits the risk cap; exits where the cash trade exits;")
    P("   lots = as many as both limits allow. Skips: unaffordable = no strike's lot fits the capital;")
    P("   risk cap = affordable but one lot's stop-out breaks the cap; cost = fees > 20% of the stop-out.")
    opt = option_setup(names, days)
    top = [pick] + sorted([l for l in results if works(l) and l != pick], key=lambda l: -results[l][0]["tot"])[:2]
    top += [best_by[k_] for k_ in sorted(best_by) if best_by[k_] not in top][:3]
    top = [("desk top-5/30min (live)", None, "smc1", "M")] + [(str(l), l, l[4], l[6]) for l in top]
    for name, lab, kd, mode in top:
        allow = DESK if lab is None else allow_of(lab)
        okc = np.isin(kind, [KINDS.index(kd)]) & allow[si, di, ki] & reg
        cand = np.flatnonzero(okc & ~np.isnan(A[:, C[f"{mode}_ex"]]))
        P(f"-- {name}: {len(cand):,} signals (setup + viable stop + regime; exit simulated)")
        P(f"   cash MIS, same variant, 6%: {fmt(run(allow, [KINDS.index(kd)], mode)[0])}")
        P(f"   {'':34s}{HDR}")
        for capital, rp in [(CAPITAL, float(x)) for x in range(1, 11)] + [(25000.0, 6.0), (50000.0, 6.0), (100000.0, 6.0)]:
            net, gate, mincap, info = option_outcomes(A, cand, mode, opt, capital, risk_pct=rp)
            g = gate[cand]
            okm = np.zeros(len(A), bool); okm[cand[g == 0]] = True
            smo, bo, _ = run(allow, [KINDS.index(kd)], mode, capital=capital, net=net, okm=okm, rp=rp)
            w = net[bo].min() if len(bo) else 0.0
            arisk = np.mean([info[i][5] for i in bo]) if len(bo) else 0.0
            P(f"   Rs {capital:>9,.0f} risk {rp:3.0f}%: ok {int((g == 0).sum()):5,d} unaff {int((g == 2).sum()):5,d} "
              f"riskcap {int((g == 3).sum()):5,d} cost {int((g == 4).sum()):4,d} |{fmt(smo)}  worst {w:7,.0f}  avg risk Rs {arisk:6,.0f}")
            if capital == CAPITAL and rp == 6.0:
                mc = mincap[cand]; mc = mc[~np.isnan(mc)]
                if len(mc):
                    P(f"   capital needed for 1 lot within 6% risk: median Rs {np.median(mc):,.0f}, p25 Rs "
                      f"{np.percentile(mc, 25):,.0f}, p75 Rs {np.percentile(mc, 75):,.0f}; share of signals tradable at "
                      f"Rs {CAPITAL:,.0f}: {100 * np.mean(mc <= CAPITAL):.1f}%, 25k {100 * np.mean(mc <= 25000):.0f}%, "
                      f"50k {100 * np.mean(mc <= 50000):.0f}%, 1 lakh {100 * np.mean(mc <= 100000):.0f}%")
                if len(bo):
                    P(f"   examples (strike, ask, lots, qty, premium Rs, stop-out Rs): "
                      f"{[tuple(round(x, 1) for x in info[i]) for i in bo[:3]]}")
    open(RESULT, "w").write("\n".join(lines) + "\n")
    print("\n".join(lines))
    pickle.dump({k_: (v[0], v[2]) for k_, v in results.items()}, open(f"{OUT}/results.pkl", "wb"))
    print(f"[report {time.time() - t0:.0f}s]")



if __name__ == "__main__":
    stage = sys.argv[1] if len(sys.argv) > 1 else "report"
    if stage == "build":
        build()
    elif stage == "features":
        features()
    elif stage == "cands":
        procs = int(sys.argv[2]) if len(sys.argv) > 2 else 3
        only = sys.argv[3].split(",") if len(sys.argv) > 3 else None
        cands(procs, only)
    elif stage == "report":
        report()
