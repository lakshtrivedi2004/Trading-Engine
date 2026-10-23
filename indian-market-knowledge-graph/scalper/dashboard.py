"""Read-only local dashboard (stdlib only). It never talks to the broker; it reads the
files the engine writes: state/status_<mode>.json, logs/trades_<mode>.csv, state/gate.json.

    python -m scalper dashboard            # http://127.0.0.1:8050
"""
from __future__ import annotations

import csv
import json
import logging
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

from .config import Config

log = logging.getLogger("scalper.dashboard")
PAGE = Path(__file__).with_name("dashboard.html")


def _read_json(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return None


def _trades(cfg: Config, mode: str) -> list[dict]:
    path = cfg.log_dir / f"trades_{mode}.csv"
    if not path.exists():
        return []
    out = []
    with path.open() as f:
        for r in csv.DictReader(f):
            for k in ("qty", "side"):
                r[k] = int(float(r[k]))
            for k in ("entry", "exit", "gross", "costs", "net", "day_pnl"):
                r[k] = float(r[k])
            out.append(r)
    return out


def _status(cfg: Config, mode: str) -> dict:
    s = _read_json(cfg.state_dir / f"status_{mode}.json")
    if not s:
        return {"mode": mode.upper(), "phase": "offline", "age": None}
    try:
        s["age"] = round((datetime.now() - datetime.fromisoformat(s["updated"])).total_seconds(), 1)
    except (KeyError, ValueError):
        s["age"] = None
    if s["phase"] not in ("stopped",) and (s["age"] is None or s["age"] > 10):
        s["phase"] = "stale"
    return s


def make_handler(cfg: Config):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, body: bytes, ctype: str, code: int = 200):
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            u = urlparse(self.path)
            mode = parse_qs(u.query).get("mode", ["paper"])[0]
            mode = mode if mode in ("paper", "live") else "paper"
            if u.path == "/":
                self._send(PAGE.read_bytes(), "text/html; charset=utf-8")
            elif u.path == "/api/status":
                self._send(json.dumps(_status(cfg, mode)).encode(), "application/json")
            elif u.path == "/api/engines":
                st = {m: _status(cfg, m) for m in ("paper", "live")}
                running = [m for m, x in st.items() if x["phase"] not in ("stopped", "stale", "offline")]
                body = {m: x["phase"] for m, x in st.items()}
                body["running"] = running[0] if running else None
                self._send(json.dumps(body).encode(), "application/json")
            elif u.path == "/api/trades":
                self._send(json.dumps(_trades(cfg, mode)).encode(), "application/json")
            elif u.path == "/api/gate":
                self._send(json.dumps(_read_json(cfg.state_dir / "gate.json") or {}).encode(), "application/json")
            else:
                self._send(b"not found", "text/plain", 404)
    return H


def serve(cfg: Config, host: str = "127.0.0.1", port: int = 8050):
    srv = ThreadingHTTPServer((host, port), make_handler(cfg))
    print(f"dashboard: http://{host}:{port}   (Ctrl+C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
