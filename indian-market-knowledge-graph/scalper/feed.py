"""Angel SmartStream (WebSocket V2) tick feed + 1-minute bar builder.

Binary packet layout (little-endian), QUOTE mode = 2:
  0 mode u8 | 1 exch u8 | 2:27 token str | 27 seq i64 | 35 exch_ts i64 ms | 43 ltp i64 paise
  51 last_qty i64 | 59 avg_px i64 | 67 day_volume i64 | 75 buy_qty f64 | 83 sell_qty f64
  91 open | 99 high | 107 low | 115 close   (i64 paise)
"""
from __future__ import annotations

import json
import logging
import queue
import struct
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import websocket

log = logging.getLogger("scalper.feed")

WS_URL = "wss://smartapisocket.angelone.in/smart-stream"
IST = timezone(timedelta(hours=5, minutes=30))
EXCH_TYPE = {"NSE": 1, "NFO": 2, "BSE": 3, "BFO": 4}


@dataclass
class Tick:
    token: str
    ts: datetime          # IST, tz-naive
    ltp: float
    day_volume: int


def parse_packet(b: bytes) -> Tick | None:
    if len(b) < 51:
        return None
    mode = b[0]
    token = b[2:27].split(b"\x00", 1)[0].decode()
    ts_ms, ltp = struct.unpack_from("<qq", b, 35)
    vol = struct.unpack_from("<q", b, 67)[0] if mode >= 2 and len(b) >= 75 else 0
    ts = datetime.fromtimestamp(ts_ms / 1000, IST).replace(tzinfo=None)
    return Tick(token, ts, ltp / 100.0, vol)


class SmartStream:
    """Background websocket thread pushing Tick objects into `out` queue; reconnects with backoff."""

    def __init__(self, jwt: str, api_key: str, client_code: str, feed_token: str,
                 subscriptions: dict[str, list[str]], out: "queue.Queue[Tick]", mode: int = 2):
        self.headers = {"Authorization": jwt, "x-api-key": api_key, "x-client-code": client_code, "x-feed-token": feed_token}
        self.subs, self.out, self.mode = subscriptions, out, mode
        self._stop = threading.Event()
        self.connected = threading.Event()
        self.last_msg = 0.0
        self._ws = None

    def start(self):
        threading.Thread(target=self._run, name="smartstream", daemon=True).start()
        threading.Thread(target=self._heartbeat, name="smartstream-hb", daemon=True).start()

    def subscribe(self, exchange: str, tokens: list[str]):
        """Add tokens on the live socket (e.g. the option bought); remembered for reconnects."""
        cur = self.subs.setdefault(exchange, [])
        new = [t for t in tokens if t not in cur]
        if not new:
            return
        cur.extend(new)
        if self._ws and self.connected.is_set():
            self._ws.send(json.dumps({"correlationID": "scalper02", "action": 1, "params": {
                "mode": self.mode, "tokenList": [{"exchangeType": EXCH_TYPE[exchange], "tokens": new}]}}))

    def stop(self):
        self._stop.set()
        if self._ws:
            self._ws.close()

    def _on_open(self, ws):
        token_list = [{"exchangeType": EXCH_TYPE[ex], "tokens": toks} for ex, toks in self.subs.items()]
        ws.send(json.dumps({"correlationID": "scalper01", "action": 1, "params": {"mode": self.mode, "tokenList": token_list}}))
        self.connected.set()
        log.info("feed connected, subscribed %d tokens", sum(len(t) for t in self.subs.values()))

    def _on_message(self, ws, msg):
        self.last_msg = time.monotonic()
        if isinstance(msg, (bytes, bytearray)):
            t = parse_packet(bytes(msg))
            if t:
                self.out.put(t)

    def _on_close(self, ws, *a):
        self.connected.clear()
        log.warning("feed closed %s", a)

    def _on_error(self, ws, err):
        log.warning("feed error: %s", err)

    def _run(self):
        backoff = 1
        while not self._stop.is_set():
            self._ws = websocket.WebSocketApp(WS_URL, header=self.headers, on_open=self._on_open,
                                              on_message=self._on_message, on_close=self._on_close, on_error=self._on_error)
            self._ws.run_forever(ping_interval=0)
            if self._stop.is_set():
                break
            time.sleep(backoff)
            backoff = min(backoff * 2, 30)

    def _heartbeat(self):
        while not self._stop.is_set():
            time.sleep(10)
            try:
                if self._ws and self.connected.is_set():
                    self._ws.send("ping")
            except Exception:  # noqa: BLE001 - reconnect loop handles it
                pass


class BarBuilder:
    """Aggregates ticks into 1-minute OHLCV bars; emits the bar when its minute closes."""

    def __init__(self):
        self.cur: dict[str, dict] = {}
        self.last_cum_vol: dict[str, int] = {}

    def seed_volume(self, token: str, cum: int):
        self.last_cum_vol[token] = cum

    def on_tick(self, t: Tick) -> dict | None:
        minute = t.ts.replace(second=0, microsecond=0)
        prev = self.last_cum_vol.get(t.token)
        dv = max(t.day_volume - prev, 0) if prev is not None and t.day_volume else 0
        if t.day_volume:
            self.last_cum_vol[t.token] = t.day_volume
        b = self.cur.get(t.token)
        done = None
        if b is not None and minute > b["ts"]:
            done = b
            b = None
        if b is None:
            b = {"token": t.token, "ts": minute, "open": t.ltp, "high": t.ltp, "low": t.ltp, "close": t.ltp, "volume": 0}
            self.cur[t.token] = b
        b["high"] = max(b["high"], t.ltp)
        b["low"] = min(b["low"], t.ltp)
        b["close"] = t.ltp
        b["volume"] += dv
        return done

    def flush_older_than(self, now: datetime) -> list[dict]:
        """Close bars for quiet symbols once the wall clock passes their minute."""
        minute = now.replace(second=0, microsecond=0)
        out = []
        for tok, b in list(self.cur.items()):
            if b["ts"] < minute:
                out.append(b)
                del self.cur[tok]
        return out
