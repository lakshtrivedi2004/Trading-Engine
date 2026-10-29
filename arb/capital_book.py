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

The ceiling (IntradayLimits.capital_ceiling, owner's request of 30 Sep 2026).
The ledger keeps compounding - it is the account's real equity - but what is
TRADED is min(ceiling, equity): a profit stays in the account untouched, a
loss shrinks the purse until profits bring it back up to the ceiling.

Live mode (owner, 11 Oct 2026): the account can hold less than the configured
amount, so what is traded is never a figure fixed at the open. The engine
re-reads the account's funds through the day (set_account_funds) and sizes on
min(ceiling, funds right now). The daily loss halt and the daily target stay
on the day's opening figure (opening_capital), so a re-read or a restart does
not move them. Paper never sets account funds and trades the ledger as before.

Paper capital set on the dashboard (owner, 28 Oct 2026): the scalper page can
name the paper purse (set_capital). The figure becomes the ledger's equity and
the ceiling, and is remembered in the file ("chosen") so every later paper
session trades it instead of the configured ceiling or live's funds.
"""
import json
import threading

from arb.instruments import now_ist
from arb.fileio import write_json


class CapitalBook:
    def __init__(self, starting, path="journal/capital.json", log=lambda *_: None, ceiling=0.0):
        self.path = path
        self.ceiling = float(ceiling or 0.0)
        self.log = log
        self.starting = float(starting)
        self._lock = threading.Lock()
        self._data = {"capital": float(starting), "opened": "", "history": [],
                      "day": "", "day_pnl": 0.0, "day_trades": 0}
        self._account_funds = None      # live only: the account's money right now
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
            write_json(self.path, self._data, indent=2)
        except Exception as e:
            self.log(f"[capital] save failed: {e}")

    def traded(self, equity):
        """The traded figure for a given equity: min(ceiling, equity)."""
        return min(equity, self.ceiling) if self.ceiling > 0 else equity

    @property
    def equity(self):
        with self._lock:
            return float(self._data.get("capital", self.starting))

    @property
    def capital(self):
        """What is traded: the equity capped at the ceiling - in live mode,
        once the account has been read, min(ceiling, the account's funds now)."""
        f = self._account_funds
        return self.traded(f if f is not None else self.equity)

    @property
    def opening_capital(self):
        """The day's opening traded figure, min(ceiling, the ledger's equity).
        The daily loss halt and the daily target use it: a re-read of the
        account in the middle of the day must not move either."""
        return self.traded(self.equity)

    @property
    def account_funds(self):
        """Live: the account's funds as last read; None in paper mode."""
        return self._account_funds

    def set_account_funds(self, funds):
        """Live: what the account holds right now. From here on what is traded
        is min(ceiling, funds): the configured amount when the account holds at
        least that much, else the account's own figure. A failed read (0 or
        less) keeps the last figure. Returns the traded capital."""
        funds = float(funds or 0.0)
        if funds > 0:
            with self._lock:
                self._account_funds = funds
        return self.capital

    @property
    def chosen(self):
        """Paper: the capital the owner set on the dashboard, 0.0 if never."""
        with self._lock:
            return float((self._data.get("chosen") or {}).get("capital") or 0.0)

    def set_capital(self, amount, note="dashboard"):
        """Paper: trade exactly `amount` from now on - it becomes the equity
        and the ceiling. A previous day still in the ledger is folded first,
        so its history row stays right; today's ledger is kept, so the trades
        already closed still count toward today's loss halt and fold in at the
        next open. Returns the traded capital."""
        amount = float(amount)
        if not amount > 0:
            raise ValueError(f"capital must be positive, got {amount!r}")
        today = now_ist().strftime("%Y-%m-%d")
        with self._lock:
            day = self._data.get("day", "")
            if day and day != today:
                self._fold_locked(day, note)
            self._data["capital"] = amount
            self._data["chosen"] = {"capital": round(amount, 2), "note": note,
                                    "at": now_ist().strftime("%Y-%m-%d %H:%M:%S")}
            self.ceiling = amount
            self._save()
        return self.capital

    def daily_target(self, pct):
        """The day's goal in rupees. A goal, not a gate - nothing stops trading
        when it is reached, because a day that runs further is a better day."""
        return self.opening_capital * pct / 100.0

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
            cap = self.traded(float(self._data.get("capital", self.starting)))
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
            return self.traded(float(self._data.get("capital", funds)))

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
            return self.traded(before), self.traded(before + folded)

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
                "capital": round(self.traded(float(self._data.get("capital", self.starting))), 2),
                "equity": round(float(self._data.get("capital", self.starting)), 2),
                "ceiling": round(self.ceiling, 2),
                "starting": round(self.starting, 2),
                "days": len(hist),
                "total_return_pct": round(
                    100.0 * (float(self._data.get("capital", self.starting)) - self.starting)
                    / self.starting, 3) if self.starting else 0.0,
                "history": hist[-30:],
            }
