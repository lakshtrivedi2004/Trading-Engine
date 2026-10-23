"""Thin Angel One SmartAPI REST client (requests only) with per-endpoint rate limiting."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import socket
import struct
import threading
import time
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

import pandas as pd
import requests

log = logging.getLogger("scalper.broker")

BASE = "https://apiconnect.angelone.in"
SCRIP_MASTER = "https://margincalculator.angelbroking.com/OpenAPI_File/files/OpenAPIScripMaster.json"

# minimum seconds between calls (Angel's published per-endpoint limits, with headroom)
MIN_INTERVAL = {
    "order": 1 / 8,      # place/modify/cancel: stay < 10 orders/sec (NSE retail-algo threshold)
    "orderbook": 1.05,
    "position": 1.05,
    "rms": 0.6,
    "candle": 0.60,
    "quote": 0.12,
    "default": 0.2,
}


def totp_now(secret: str, step: int = 30, digits: int = 6) -> str:
    """RFC 6238 TOTP (what pyotp.TOTP(secret).now() returns)."""
    key = base64.b32decode(secret.upper().replace(" ", "") + "=" * (-len(secret) % 8))
    h = hmac.new(key, struct.pack(">Q", int(time.time()) // step), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    return str((struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 10 ** digits).zfill(digits)


class RateLimiter:
    def __init__(self):
        self._last: dict[str, float] = {}
        self._lock = threading.Lock()

    def wait(self, key: str):
        gap = MIN_INTERVAL.get(key, MIN_INTERVAL["default"])
        with self._lock:
            now = time.monotonic()
            sleep = self._last.get(key, 0) + gap - now
            if sleep > 0:
                time.sleep(sleep)
            self._last[key] = time.monotonic()


class BrokerError(RuntimeError):
    pass


class AngelClient:
    def __init__(self, env: dict[str, str]):
        self.api_key = env["ANGLE_ONE_API"]
        self.client_code = env["ANGLE_ONE_ID"]
        self.pin = env["ANGLE_ONE_PIN"]
        self.totp_secret = env["ANGLE_ONE_TOTP"]
        self.jwt = self.feed_token = self.refresh_token = None
        self.last_raw = ""
        self.rl = RateLimiter()
        self.s = requests.Session()
        self.s.headers.update({
            "Content-Type": "application/json", "Accept": "application/json",
            "X-UserType": "USER", "X-SourceID": "WEB",
            "X-ClientLocalIP": _local_ip(), "X-ClientPublicIP": _public_ip(env.get("STATIC_IP", "")),
            "X-MACAddress": ":".join(f"{(uuid.getnode() >> b) & 0xff:02x}" for b in range(40, -1, -8)),
            "X-PrivateKey": self.api_key,
        })

    # ------------------------------------------------------------ plumbing
    def _call(self, method: str, path: str, body: dict | None = None, rl: str = "default", retries: int = 2):
        for attempt in range(retries + 1):
            self.rl.wait(rl)
            try:
                r = self.s.request(method, BASE + path, json=body, timeout=10)
                self.last_raw = f"HTTP {r.status_code} {r.text}"
                j = r.json() if r.content else {}
            except (requests.RequestException, ValueError) as e:
                # Angel answers an over-limit burst with an empty / non-JSON body -> back off and retry
                if attempt == retries:
                    raise BrokerError(f"{path}: {e}") from e
                time.sleep(1.0 * (attempt + 1))
                continue
            if not isinstance(j, dict):
                j = {"data": j}
            # Angel answers in two shapes: {"status", "errorcode"} from the API and
            # {"success", "errorCode"} from the gateway (e.g. IP not whitelisted) - both count as failures
            failed = (j.get("status") is False or j.get("success") is False
                      or str(j.get("status", "")).lower() == "false"
                      or (r.status_code >= 400 and not j.get("data")))
            if failed:
                code = j.get("errorcode") or j.get("errorCode") or r.status_code
                msg = f"{path}: {code} {j.get('message')} | raw: {r.text[:300]}"
                # rate-limit / transient -> retry, everything else -> raise
                if attempt < retries and (r.status_code in (429, 500, 502, 503) or "rate" in str(j.get("message", "")).lower()):
                    time.sleep(0.7 * (attempt + 1))
                    continue
                raise BrokerError(msg)
            return j.get("data")
        raise BrokerError(f"{path}: retries exhausted")

    # ------------------------------------------------------------ session
    def login(self):
        data = self._call("POST", "/rest/auth/angelbroking/user/v1/loginByPassword",
                          {"clientcode": self.client_code, "password": self.pin, "totp": totp_now(self.totp_secret)})
        self.jwt, self.feed_token, self.refresh_token = data["jwtToken"], data["feedToken"], data["refreshToken"]
        self.s.headers["Authorization"] = f"Bearer {self.jwt}"
        log.info("logged in as %s", self.client_code)

    def logout(self):
        """NSE retail-algo rules require API sessions to be logged out daily."""
        try:
            self._call("POST", "/rest/secure/angelbroking/user/v1/logout", {"clientcode": self.client_code})
            log.info("logged out")
        except BrokerError as e:
            log.warning("logout failed: %s", e)

    def rms(self) -> dict:
        return self._call("GET", "/rest/secure/angelbroking/user/v1/getRMS", rl="rms") or {}

    # ------------------------------------------------------------ market data
    def candles(self, token: str, start: datetime, end: datetime, exchange: str = "NSE", interval: str = "ONE_MINUTE") -> pd.DataFrame:
        """1-minute candles; Angel caps ONE_MINUTE requests at 30 days so we chunk."""
        out = []
        cur = start
        while cur < end:
            nxt = min(cur + timedelta(days=29), end)
            data = self._call("POST", "/rest/secure/angelbroking/historical/v1/getCandleData",
                              {"exchange": exchange, "symboltoken": token, "interval": interval,
                               "fromdate": cur.strftime("%Y-%m-%d %H:%M"), "todate": nxt.strftime("%Y-%m-%d %H:%M")},
                              rl="candle", retries=5) or []
            out.extend(data)
            cur = nxt + timedelta(minutes=1)
        if not out:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = pd.DataFrame(out, columns=["ts", "open", "high", "low", "close", "volume"])
        df["ts"] = pd.to_datetime(df["ts"]).dt.tz_localize(None)
        return df.drop_duplicates("ts").set_index("ts").sort_index().astype(float)

    def quote(self, tokens: list[str], exchange: str = "NSE", mode: str = "LTP") -> dict:
        data = self._call("POST", "/rest/secure/angelbroking/market/v1/quote",
                          {"mode": mode, "exchangeTokens": {exchange: tokens}}, rl="quote") or {}
        return {f["symbolToken"]: f for f in data.get("fetched", [])}

    # ------------------------------------------------------------ orders
    def place_order(self, *, symbol: str, token: str, side: str, qty: int, order_type: str, price: float = 0.0,
                    trigger: float = 0.0, variety: str = "NORMAL", exchange: str = "NSE", product: str = "INTRADAY") -> str:
        body = {"variety": variety, "tradingsymbol": symbol, "symboltoken": token, "transactiontype": side,
                "exchange": exchange, "ordertype": order_type, "producttype": product, "duration": "DAY",
                "price": f"{price:.2f}", "triggerprice": f"{trigger:.2f}", "quantity": str(int(qty)),
                "squareoff": "0", "stoploss": "0"}
        data = self._call("POST", "/rest/secure/angelbroking/order/v1/placeOrder", body, rl="order", retries=0)
        oid = data.get("orderid") if isinstance(data, dict) else None
        if not oid:
            raise BrokerError(f"placeOrder returned no order id: {self.last_raw[:400]}")
        log.info("ORDER %s %s %s x%d @%.2f trg %.2f -> %s", order_type, side, symbol, qty, price, trigger, oid)
        return oid

    def modify_order(self, *, order_id: str, symbol: str, token: str, qty: int, order_type: str, price: float,
                     trigger: float = 0.0, variety: str = "NORMAL", exchange: str = "NSE", product: str = "INTRADAY"):
        body = {"variety": variety, "orderid": order_id, "ordertype": order_type, "producttype": product,
                "duration": "DAY", "price": f"{price:.2f}", "triggerprice": f"{trigger:.2f}", "quantity": str(int(qty)),
                "tradingsymbol": symbol, "symboltoken": token, "exchange": exchange}
        self._call("POST", "/rest/secure/angelbroking/order/v1/modifyOrder", body, rl="order", retries=1)

    def cancel_order(self, order_id: str, variety: str = "NORMAL"):
        self._call("POST", "/rest/secure/angelbroking/order/v1/cancelOrder", {"variety": variety, "orderid": order_id},
                   rl="order", retries=1)

    def order_book(self) -> list[dict]:
        return self._call("GET", "/rest/secure/angelbroking/order/v1/getOrderBook", rl="orderbook") or []

    def order(self, order_id: str) -> dict | None:
        return next((o for o in self.order_book() if o.get("orderid") == order_id), None)

    def positions(self) -> list[dict]:
        return self._call("GET", "/rest/secure/angelbroking/order/v1/getPosition", rl="position") or []


def _public_ip(static_ip: str = "") -> str:
    """X-ClientPublicIP must be the internet-facing IP Angel has whitelisted - never a 192.168.x.x LAN address.
    STATIC_IP wins (run.sh and the engine verify it matches reality); otherwise look it up."""
    if static_ip:
        return static_ip
    live = ""
    for url in ("https://api.ipify.org", "https://checkip.amazonaws.com"):
        try:
            live = requests.get(url, timeout=5).text.strip()
            break
        except requests.RequestException:
            continue
    if live and static_ip and live != static_ip:
        log.warning("public IP %s differs from STATIC_IP %s - Angel will reject orders", live, static_ip)
    return live or static_ip or _local_ip()


def _local_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except OSError:
        return "127.0.0.1"


# ---------------------------------------------------------------- instruments

def load_instruments(cache_dir: Path, symbols: list[str], exchange: str = "NSE") -> dict[str, dict]:
    """symbol -> {token, tradingsymbol, tick, lot}. Scrip master is cached once per day."""
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = cache_dir / f"scrip_master_{date.today():%Y%m%d}.json"
    if not path.exists():
        log.info("downloading scrip master ...")
        r = requests.get(SCRIP_MASTER, timeout=60)
        r.raise_for_status()
        path.write_bytes(r.content)
        for old in cache_dir.glob("scrip_master_*.json"):
            if old != path:
                old.unlink()
    rows = json.loads(path.read_text())
    want = {f"{s}-EQ": s for s in symbols}
    out = {}
    for row in rows:
        if row.get("exch_seg") == exchange and row.get("symbol") in want:
            tick = float(row.get("tick_size") or 5) / 100.0          # scrip master stores paise
            out[want[row["symbol"]]] = {"token": row["token"], "tradingsymbol": row["symbol"],
                                        "tick": tick if tick > 0 else 0.05, "lot": int(float(row.get("lotsize") or 1))}
    missing = set(symbols) - set(out)
    if missing:
        log.warning("not found in scrip master (skipped): %s", ", ".join(sorted(missing)))
    return out
