"""The purse, and the fact that it grows.

Capital is not a constant in this system. At the end of each session the day's
realised profit or loss is folded back in, so tomorrow's position sizing is
computed on what the account is actually worth. That is the whole point of a
daily percentage target: 10% of a growing number is a growing number.

Persisted to journal/capital.json so a restart - or a new day - picks up the
real figure rather than the dataclass default. Only realised, closed P&L is ever
folded in; an open position's mark-to-market is not money yet.

The day ledger. Every closed trade is written to the ledger the moment it
closes (`record`), and the day's total is folded into capital ONCE, when the
next trading day opens (`open_day`) or after the close. So:

  * sizing, the risk cap and the daily target all use the day's OPENING
    capital - today's profit is tomorrow's capital, as the owner asked;
  * a restart in the middle of the day resumes the day's P&L and trade count
    instead of starting from zero, so the daily loss halt cannot be reset by
    restarting the bot;
  * a crash loses nothing: the ledger is on disk after every trade.
"""
import json
import os
import threading

from arb.instruments import now_ist


class CapitalBook:
    def __init__(self, starting, path="journal/capital.json", log=lambda *_: None):
        self.path = path
        self.log = log
        self.starting = float(starting)
        self._lock = threading.Lock()
        self._data = {"capital": float(starting), "opened": "", "history": [],
                      "day": "", "day_pnl": 0.0, "day_trades": 0}
        self._load()

    def _load(self):
        try:
            with open(self.path) as fh:
                d = json.load(fh)
            if isinstance(d, dict) and "capital" in d:
                self._data = d
        except Exception:
            pass

    def _save(self):
        try:
            os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w") as fh:
                json.dump(self._data, fh, indent=2)
            os.replace(tmp, self.path)
        except Exception as e:
            self.log(f"[capital] save failed: {e}")

    @property
    def capital(self):
        with self._lock:
            return float(self._data.get("capital", self.starting))

    def daily_target(self, pct):
        """The day's goal in rupees. A goal, not a gate - nothing stops trading
        when it is reached, because a day that runs further is a better day."""
        return self.capital * pct / 100.0

    def max_risk_per_trade(self, pct):
        return self.capital * pct / 100.0

    # ── the day ledger ──────────────────────────────────────────────────────
    @property
    def day_pnl(self):
        with self._lock:
            return float(self._data.get("day_pnl", 0.0))

    @property
    def day_trades(self):
        with self._lock:
            return int(self._data.get("day_trades", 0))

    def open_day(self, today=None, note=""):
        """Start (or resume) a trading day. A previous day's unfolded P&L is
        folded into capital first. Returns (folded_pnl, capital_now)."""
        today = today or now_ist().strftime("%Y-%m-%d")
        folded = 0.0
        with self._lock:
            day = self._data.get("day", "")
            if day and day != today:
                folded = self._fold_locked(day, note)
            if self._data.get("day") != today:
                self._data["day"] = today
                self._data["day_pnl"] = 0.0
                self._data["day_trades"] = 0
            cap = float(self._data.get("capital", self.starting))
            self._save()
        return folded, cap

    def record(self, net_pnl, count=True):
        """One closed trade (or, with count=False, a partial exit of one). On
        disk immediately; folded into capital later."""
        with self._lock:
            if not self._data.get("day"):
                self._data["day"] = now_ist().strftime("%Y-%m-%d")
            self._data["day_pnl"] = float(self._data.get("day_pnl", 0.0)) + float(net_pnl)
            if count:
                self._data["day_trades"] = int(self._data.get("day_trades", 0)) + 1
            self._save()

    def sync_to_funds(self, funds, note="broker funds"):
        """Live mode: the day opens on what the broker says the account holds,
        not on a figure this process computed. Only on a fresh day - a restart
        mid-session keeps the day's opening capital so the risk cap and the
        daily loss halt do not move under an open day."""
        funds = float(funds)
        if funds <= 0:
            return self.capital
        with self._lock:
            if int(self._data.get("day_trades", 0)) == 0 and abs(float(self._data.get("day_pnl", 0.0))) < 1e-9:
                self._data["capital"] = funds
                self._data["synced"] = {"date": now_ist().strftime("%Y-%m-%d"),
                                        "funds": round(funds, 2), "note": note}
                self._save()
            return float(self._data.get("capital", funds))

    def close_day(self, note=""):
        """After the session: fold today's ledger into capital now. Safe to call
        more than once - the second call has nothing left to fold."""
        with self._lock:
            day = self._data.get("day", "")
            if not day:
                return 0.0, float(self._data.get("capital", self.starting))
            before = float(self._data.get("capital", self.starting))
            folded = self._fold_locked(day, note)
            # Stay on the same day, with an empty ledger, so a late restart
            # today neither folds twice nor loses the halt.
            self._data["day"] = day
            self._data["day_pnl"] = 0.0
            self._save()
            return before, before + folded

    def _fold_locked(self, day, note):
        pnl = float(self._data.get("day_pnl", 0.0))
        trades = int(self._data.get("day_trades", 0))
        if trades == 0 and abs(pnl) < 1e-9:
            return 0.0
        before = float(self._data.get("capital", self.starting))
        after = before + pnl
        self._data["capital"] = after
        self._data.setdefault("history", []).append({
            "date": day, "opening": round(before, 2), "pnl": round(pnl, 2),
            "closing": round(after, 2),
            "return_pct": round(100.0 * pnl / before, 3) if before else 0.0,
            "trades": trades, "note": note,
        })
        self._data["day_pnl"] = 0.0
        self._data["day_trades"] = 0
        return pnl

    def fold_in(self, realised_pnl, trades=0, note=""):
        """End of session: the day's realised P&L becomes tomorrow's capital."""
        with self._lock:
            before = float(self._data.get("capital", self.starting))
            after = before + float(realised_pnl)
            self._data["capital"] = after
            self._data.setdefault("history", []).append({
                "date": now_ist().strftime("%Y-%m-%d"),
                "opening": round(before, 2),
                "pnl": round(float(realised_pnl), 2),
                "closing": round(after, 2),
                "return_pct": round(100.0 * realised_pnl / before, 3) if before else 0.0,
                "trades": trades,
                "note": note,
            })
            self._save()
            return before, after

    def summary(self):
        with self._lock:
            hist = self._data.get("history", [])
            return {
                "capital": round(float(self._data.get("capital", self.starting)), 2),
                "starting": round(self.starting, 2),
                "days": len(hist),
                "total_return_pct": round(
                    100.0 * (float(self._data.get("capital", self.starting)) - self.starting)
                    / self.starting, 3) if self.starting else 0.0,
                "history": hist[-30:],
            }
