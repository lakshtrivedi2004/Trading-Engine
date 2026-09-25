#!/usr/bin/env python3
"""Replay the sideways + premium-chart rules on a recorded book (research/PAPERS.md 0e).

    python tools/premium_replay.py data/book_20260917_123059.f4

Builds 1-minute mid bars per option from a TickRecorder file, proxies the index
with put-call parity, then takes one trade at a time in the direction of the last
15 minutes, holding 30 minutes, net of spread and ~Rs 75 fees per lot.
"""
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from arb.config import IntradayLimits
from arb.regime import efficiency, premium_gate

path = sys.argv[1]; m = json.load(open(path.replace(".f4", ".meta.json")))
n = m["n_slots"]; raw = np.memmap(path, dtype=np.float32, mode="r")
a = raw[: raw.size // (n * 8) * n * 8].reshape(-1, n, 8)[239::240]   # 4 Hz -> minute closes
bid, ask = a[:, :, 1].astype(float), a[:, :, 2].astype(float)
mid = np.where((bid > 0) & (ask > 0), (bid + ask) / 2, np.nan)
L, W, H, FEE = IntradayLimits(), 15, 30, 75.0
for label, rules in (("baseline", False), ("sideways + premium rules", True)):
    pnl = []
    for e in m["expiries"]:
        K, ce, pe, lot = np.array(e["strikes"]), e["ce_slots"], e["pe_slots"], e["lot_size"]
        j = np.nanargmin(np.abs(mid[:, ce] - mid[:, pe]), axis=1); r = np.arange(len(j))
        F = K[j] + mid[:, ce][r, j] - mid[:, pe][r, j]
        t = W
        while t < len(F) - H:
            up = F[t] > F[t - W]; s = (ce if up else pe)[np.argmin(np.abs(K - F[t]))]
            X, sp = mid[:, s], ask[:, s] - bid[:, s]
            # Like ContractSelector: spot outside the subscribed ladder is not tradable.
            ok = K[0] <= F[t] <= K[-1] and not np.isnan(X[t - W:t + H + 1]).any()
            if ok and rules:
                n_ = L.sideways_bars
                ok = (efficiency(list(F[t - n_:t + 1])) * n_ ** 0.5 >= L.min_trend_efficiency
                      and premium_gate(list(X[:t + 1]), FEE / lot, L) == "")
            if not ok:
                t += 1; continue
            pnl.append(((X[t + H] - sp[t + H] / 2) - (X[t] + sp[t] / 2)) * lot - FEE); t += H + 1
    p = np.array(pnl)
    print(f"{label:26s} trades {len(p):3d}  win {100 * (p > 0).mean() if len(p) else 0:3.0f}%  "
          f"total Rs {p.sum():8,.0f}  worst Rs {p.min() if len(p) else 0:7,.0f}")
