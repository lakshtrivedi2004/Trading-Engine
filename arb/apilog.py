"""Every SmartAPI REST call, timed and written to logs/api_calls.csv.

Rate limits are per client code and every caller shares them, so the only way
to know who spent the budget is to record each call where it is made. The
wrapper sits on the SmartConnect instance AngelSession creates, so the candle
store, the stock desk, the broker and the margin service are all covered
without touching them. Rows go through a queue to a writer thread: the caller
never waits on the disk, and a failed write never fails the call.

Columns: ts (epoch), iso, pid, thread, endpoint, ms, outcome, detail.
outcome is ok | refused | rate_limited | error.
"""
import os
import queue
import threading
import time
from datetime import datetime

from arb.candles import _rate_limited

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PATH = os.path.join(ROOT, "logs", "api_calls.csv")
HEADER = "ts,iso,pid,thread,endpoint,ms,outcome,detail\n"

# The REST methods this codebase calls. Websocket traffic is not REST and is
# not rate limited per call, so it is not here.
ENDPOINTS = ("generateSession", "getfeedToken", "ltpData", "getCandleData",
             "placeOrder", "placeOrderFullResponse", "modifyOrder", "cancelOrder",
             "individual_order_details", "orderBook", "position", "rmsLimit",
             "getMarginApi")

_q = queue.SimpleQueue()
_writer = None
_writer_lock = threading.Lock()


def _outcome(result, err):
    if err is not None:
        return ("rate_limited" if _rate_limited(err) else "error"), str(err)[:120]
    if isinstance(result, dict) and result.get("status") is False:
        msg = str(result.get("message", ""))
        return ("rate_limited" if _rate_limited(msg) else "refused"), msg[:120]
    if result is None:
        return "error", "no response"
    return "ok", ""


def _write_loop(path):
    while True:
        rows = [_q.get()]
        while True:
            try:
                rows.append(_q.get_nowait())
            except queue.Empty:
                break
        try:
            new = not os.path.exists(path)
            with open(path, "a") as fh:
                if new:
                    fh.write(HEADER)
                fh.writelines(rows)
        except OSError:
            pass


def _start_writer(path):
    global _writer
    with _writer_lock:
        if _writer is None:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            _writer = threading.Thread(target=_write_loop, args=(path,),
                                       name="apilog", daemon=True)
            _writer.start()


def _wrap(name, fn):
    def call(*a, **kw):
        t0 = time.time()
        result, err = None, None
        try:
            result = fn(*a, **kw)
            return result
        except Exception as e:
            err = e
            raise
        finally:
            out, detail = _outcome(result, err)
            iso = datetime.fromtimestamp(t0).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3]
            detail = detail.replace(",", ";").replace("\n", " ")
            _q.put(f"{t0:.3f},{iso},{os.getpid()},{threading.current_thread().name},"
                   f"{name},{(time.time() - t0) * 1000:.0f},{out},{detail}\n")
    call.__wrapped__ = fn
    return call


def instrument(api, path=PATH):
    """Wrap the REST methods of one SmartConnect instance, once. Returns api."""
    if api is None or getattr(api, "_apilog", False):
        return api
    for name in ENDPOINTS:
        fn = getattr(api, name, None)
        if callable(fn):
            setattr(api, name, _wrap(name, fn))
    api._apilog = True
    _start_writer(path)
    return api
