"""Every price of a BTST's option, from the buy to the next session's close
(owner, 26 Oct 2026) - to study where targets would have been hit.

While a trade is recorded each websocket tick (and each LTP call) of its
option is appended to a local file, .cache/btst_ticks/<job_id>.csv
("epoch_ms,price", a line when the price changes or every 30 s). The
recording runs from the fill until market_close (15:45) of the next trading
day - past the exit too. Then the desk (btst/strategy.py) reads it back, makes
1-minute bars (gaps - the desk down - filled from Angel's 1-minute candles),
gzips the ticks and stores both, with the trade's events, in MongoDB
(btst_ticks, by job id); the local file is removed once the copy is checked.

A crash or a restart loses no tick already written. The minutes the desk was
down (or the socket silent) are found right after a start and every few
minutes, and filled from Angel's 1-minute candles into a cache beside the
file (<job_id>.candles.json: the bars, and every minute already asked for, so
a minute with no trade is not asked again) - the page's chart and figures use
ticks + candles while the recording runs, and the final save reuses them.
"""
import gzip
import json
import os
import threading

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FOLDER = os.path.join(ROOT, ".cache", "btst_ticks")


class TickRecorder:
    def __init__(self, folder=FOLDER, keep_sec=30.0):
        self.folder, self.keep_sec = folder, float(keep_sec)
        self.lock = threading.Lock()
        self.last = {}                  # job_id -> (ms, price) of the last line written

    def path(self, job_id):
        return os.path.join(self.folder, f"{job_id}.csv")

    def record(self, job_id, ts, price):
        """One price at epoch seconds `ts`: written when it changed, or after keep_sec."""
        if not price or price <= 0:
            return False
        ms, price = int(float(ts) * 1000), round(float(price), 2)
        with self.lock:
            prev = self.last.get(job_id)
            if prev and prev[1] == price and ms - prev[0] < self.keep_sec * 1000:
                return False
            os.makedirs(self.folder, exist_ok=True)
            with open(self.path(job_id), "a") as fh:
                fh.write(f"{ms},{price}\n")
            self.last[job_id] = (ms, price)
        return True

    def read(self, job_id):
        """[(epoch_ms, price)] in time order."""
        out = []
        try:
            with open(self.path(job_id)) as fh:
                for line in fh:
                    a, _, b = line.strip().partition(",")
                    try:
                        out.append((int(a), float(b)))
                    except ValueError:
                        continue
        except FileNotFoundError:
            return []
        out.sort()
        return out

    def candles_path(self, job_id):
        return os.path.join(self.folder, f"{job_id}.candles.json")

    def read_candles(self, job_id):
        """{"bars": {minute_ms: bar}, "checked": [minute_ms]} - Angel's candles for the gaps."""
        try:
            with open(self.candles_path(job_id)) as fh:
                d = json.load(fh)
            return {"bars": {int(k): v for k, v in (d.get("bars") or {}).items()},
                    "checked": set(int(x) for x in d.get("checked") or [])}
        except (FileNotFoundError, ValueError):
            return {"bars": {}, "checked": set()}

    def save_candles(self, job_id, data):
        os.makedirs(self.folder, exist_ok=True)
        tmp = self.candles_path(job_id) + ".tmp"
        with open(tmp, "w") as fh:
            json.dump({"bars": {str(k): v for k, v in data["bars"].items()},
                       "checked": sorted(data["checked"])}, fh)
        os.replace(tmp, self.candles_path(job_id))        # never a half-written cache after a crash

    def all_bars(self, job_id):
        """1-minute bars from the ticks, the gaps from the cached candles."""
        return merge(bars(self.read(job_id)), list(self.read_candles(job_id)["bars"].values()))

    def raw(self, job_id):
        try:
            with open(self.path(job_id), "rb") as fh:
                return fh.read()
        except FileNotFoundError:
            return b""

    def compress(self, job_id):
        return gzip.compress(self.raw(job_id), 9)

    def remove(self, job_id):
        with self.lock:
            self.last.pop(job_id, None)
            for f in (self.path(job_id), self.candles_path(job_id)):
                try:
                    os.remove(f)
                except FileNotFoundError:
                    pass


def bars(ticks, minute_ms=60_000):
    """1-minute bars [{t, o, h, l, c, n}] (t: the minute's start, epoch ms) from [(ms, price)]."""
    out, cur = [], None
    for ms, p in ticks:
        t = ms - ms % minute_ms
        if cur is None or cur["t"] != t:
            cur = {"t": t, "o": p, "h": p, "l": p, "c": p, "n": 1, "src": "ticks"}
            out.append(cur)
        else:
            cur["h"], cur["l"], cur["c"] = max(cur["h"], p), min(cur["l"], p), p
            cur["n"] += 1
    return out


def merge(tick_bars, candle_bars):
    """Tick bars, with the minutes they lack taken from Angel's candles."""
    have = {b["t"] for b in tick_bars}
    out = list(tick_bars) + [dict(b, src="candle") for b in candle_bars if b["t"] not in have]
    out.sort(key=lambda b: b["t"])
    return out


def unzip(gz):
    """[(ms, price)] from a stored gzip of the local file."""
    out = []
    for line in gzip.decompress(bytes(gz)).decode().splitlines():
        a, _, b = line.partition(",")
        try:
            out.append((int(a), float(b)))
        except ValueError:
            continue
    return out
