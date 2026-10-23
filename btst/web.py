"""The BTST page and its JSON, on its own port (config/btst.json "port", 9000).

    GET  /             the page: mode, the open trade, every day's trade, the
                       statistics per mode, the last channel messages read
    GET  /api/state    the same as JSON
    POST /api/mode?to=paper|live   switch mode - refused while a trade is open
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from btst import stats

PAGE = r"""<!DOCTYPE html>
<meta charset="utf-8">
<title>BTST Desk</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root{--ink:#202122;--dim:#54595d;--rule:#a2a9b1;--hair:#c8ccd1;--bg:#fff;--panel:#f8f9fa;--up:#14866d;--down:#d33;--warn:#b07d00}
  @media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--ink:#e8e6e3;--dim:#9aa0a6;--rule:#3a3f44;--hair:#33383d;--bg:#14181c;--panel:#1b2025;--up:#3ecf9c;--down:#ff6b6b;--warn:#f0c24a}}
  :root[data-theme="dark"]{--ink:#e8e6e3;--dim:#9aa0a6;--rule:#3a3f44;--hair:#33383d;--bg:#14181c;--panel:#1b2025;--up:#3ecf9c;--down:#ff6b6b;--warn:#f0c24a}
  *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,"Segoe UI",Roboto,sans-serif;font-variant-numeric:tabular-nums}
  .wrap{max-width:1180px;margin:0 auto;padding:22px 16px 60px}
  h1{font-family:Georgia,serif;font-weight:400;font-size:28px;margin:0 0 4px;border-bottom:1px solid var(--rule);padding-bottom:6px}
  h2{font-family:Georgia,serif;font-weight:400;font-size:20px;margin:26px 0 8px;border-bottom:1px solid var(--rule);padding-bottom:4px}
  .sub{color:var(--dim);font-size:13px;margin:2px 0}
  .grid{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));margin-top:10px}
  .box{border:1px solid var(--hair);background:var(--panel);padding:9px 11px}
  .k{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--dim)} .v{font-size:21px}
  .tbl{overflow-x:auto} table{border-collapse:collapse;width:100%;font-size:13px;margin-top:6px}
  th,td{border:1px solid var(--hair);padding:4px 7px;text-align:right;white-space:nowrap}
  th{background:var(--panel);text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--dim)}
  td:first-child,th:first-child{text-align:left} td.l{text-align:left;white-space:normal}
  .up{color:var(--up)} .down{color:var(--down)} .warn{color:var(--warn)} .dim{color:var(--dim)}
  .btn{border:1px solid var(--hair);background:var(--panel);color:var(--ink);padding:5px 11px;font:inherit;font-size:13px;cursor:pointer}
  .live{color:#fff;background:var(--down);padding:1px 8px} .paper{background:var(--panel);border:1px solid var(--hair);padding:1px 8px}
  pre{white-space:pre-wrap;margin:0;font:12px/1.4 ui-monospace,Menlo,monospace}
</style>
<div class="wrap">
  <h1>BTST Desk</h1>
  <p class="sub" id="sub">connecting…</p>
  <p id="ctl"></p>
  <h2>Open trade</h2><div id="open"></div>
  <h2>Statistics</h2><div id="stats"></div>
  <h2>Every day</h2><div class="tbl" id="days"></div>
  <h2>Channel messages read (15:15 – 15:30)</h2><div class="tbl" id="msgs"></div>
</div>
<script>
const esc = s => String(s ?? "").replace(/[<>&]/g, c => ({"<":"&lt;",">":"&gt;","&":"&amp;"}[c]));
const n0 = x => x == null || isNaN(x) ? "–" : Number(x).toLocaleString("en-IN", {maximumFractionDigits: 0});
const n2 = x => x == null || isNaN(x) ? "–" : Number(x).toLocaleString("en-IN", {minimumFractionDigits: 2, maximumFractionDigits: 2});
const cls = x => x > 0 ? "up" : (x < 0 ? "down" : "");
const rs = x => x == null ? "–" : (x < 0 ? "−₹" : "₹") + n0(Math.abs(x));
let BUSY = false;
async function switchMode(to){
  if(!confirm(to === "live" ? "Switch BTST to LIVE - real orders with real money?" : "Switch BTST back to PAPER?")) return;
  BUSY = true;
  const r = await (await fetch("/api/mode?to=" + to, {method: "POST"})).json().catch(e => ({ok:false, error:String(e)}));
  BUSY = false;
  alert(r.ok ? "BTST is now " + r.mode.toUpperCase() : "Not switched: " + r.error);
}
function statBlock(s){
  const box = (k, v, c) => `<div class="box"><div class="k">${k}</div><div class="v ${c||""}">${v}</div></div>`;
  return `<p class="sub"><b>${esc(s.mode.toUpperCase())}</b> · ${s.trades} closed trade(s)${s.open_or_failed ? ` · ${s.open_or_failed} open / not traded` : ""}</p>
  <div class="grid">${box("Net P&L", rs(s.net_pnl), cls(s.net_pnl))}${box("Win rate", s.trades ? n2(s.win_rate) + "%" : "–")}
  ${box("Wins / losses", s.wins + " / " + s.losses)}${box("Avg per trade", rs(s.avg_pnl), cls(s.avg_pnl))}
  ${box("Avg % of invested", s.trades ? n2(s.avg_pct) + "%" : "–", cls(s.avg_pct))}${box("Best / worst", rs(s.best) + " / " + rs(s.worst))}
  ${box("Profit factor", s.profit_factor == null ? "–" : n2(s.profit_factor))}${box("Max drawdown", rs(s.max_drawdown))}
  ${box("Target hit / 15:14 exit", s.target_hits + " / " + s.time_exits)}${box("Averaged down", s.averaged)}
  ${box("Invested (sum)", rs(s.invested_total))}${box("Charges", rs(s.charges))}</div>`;
}
async function tick(){
  try{
    const d = await (await fetch("/api/state")).json();
    const live = d.mode === "live";
    document.getElementById("sub").innerHTML = `${esc(d.now)} IST · mode <span class="${live ? "live" : "paper"}">${esc(d.mode.toUpperCase())}</span>
      · channel ${esc(d.channel)} · scans ${esc(d.window)} IST · ${esc(d.note)}`;
    document.getElementById("ctl").innerHTML = d.open_job ? `<span class="dim">Mode can be switched once the open trade is closed.</span>`
      : `<button class="btn" ${BUSY ? "disabled" : ""} onclick="switchMode('${live ? "paper" : "live"}')">Switch to ${live ? "PAPER" : "LIVE"}</button>`;
    const j = d.open_job;
    document.getElementById("open").innerHTML = !j ? `<p class="dim">No trade open.</p>` : (() => {
      const p = j.position || {}, c = j.contract || {}, dec = j.decision || {};
      const pnl = j.last_ltp && p.avg_price ? (j.last_ltp - p.avg_price) * p.qty : null;
      return `<div class="grid">
        <div class="box"><div class="k">Contract</div><div class="v" style="font-size:16px">${esc(c.symbol)}</div><div class="dim">${esc(c.exchange)} · expiry ${esc(c.expiry)}</div></div>
        <div class="box"><div class="k">Qty @ average</div><div class="v">${n0(p.qty)} @ ${n2(p.avg_price)}</div><div class="dim">invested ${rs(p.invested)}</div></div>
        <div class="box"><div class="k">Target</div><div class="v">${n2(j.target_price)}</div><div class="dim">${j.soft_target ? "watched (order refused)" : "limit order at Angel"}</div></div>
        <div class="box"><div class="k">LTP / open P&L</div><div class="v ${cls(pnl)}">${n2(j.last_ltp)} · ${rs(pnl)}</div><div class="dim">at ${esc(j.last_ltp_at || "")}</div></div>
        <div class="box"><div class="k">Next-day decision</div><div class="v" style="font-size:16px">${esc(dec.scenario || "at the open")}</div><div class="dim">${dec.pnl_pct != null ? "opened " + n2(dec.pnl_pct) + "%" : ""}</div></div>
      </div><p class="sub">${esc(j.job_id)} · signal: ${esc((j.signal||{}).indices)} ${esc((j.signal||{}).strike_price)} ${esc((j.signal||{}).side)} @ ${esc((j.signal||{}).price)}</p>`; })();
    document.getElementById("stats").innerHTML = (d.stats || []).map(statBlock).join("")
      + `<p class="sub">Trading days with no signal (no trade): ${d.no_signal_days || 0}</p>`;
    const rows = d.days || [];
    document.getElementById("days").innerHTML = !rows.length ? `<p class="dim">No BTST yet.</p>` :
      `<table><tr><th>Date</th><th>Mode</th><th>Signal</th><th>Contract</th><th>Qty</th><th>Avg</th><th>Invested</th><th>Target</th>
       <th>Next day</th><th>Exit</th><th>Exit price</th><th>Net</th><th>%</th><th>Status</th></tr>` +
      rows.map(r => `<tr class="${r.job_id ? "" : "dim"}"><td>${esc(r.date)}</td><td>${esc(r.mode)}</td><td class="l">${esc(r.signal)}</td><td>${esc(r.symbol || "–")}</td>
        <td>${n0(r.qty)}</td><td>${n2(r.avg_price)}</td><td>${rs(r.invested)}</td><td>${n2(r.target)}</td><td>${esc(r.scenario || "")}</td>
        <td>${esc(r.exit_reason || "")}</td><td>${n2(r.exit_price)}</td><td class="${cls(r.net_pnl)}">${rs(r.net_pnl)}</td>
        <td class="${cls(r.pnl_pct)}">${r.pnl_pct == null ? "–" : n2(r.pnl_pct) + "%"}</td>
        <td class="l">${esc(r.status)}${r.reason ? " – " + esc(r.reason) : ""}</td></tr>`).join("") + `</table>`;
    const ms = d.messages || [];
    document.getElementById("msgs").innerHTML = !ms.length ? `<p class="dim">Nothing read yet.</p>` :
      `<table><tr><th>Time</th><th>Signal?</th><th>Why</th><th>Message</th></tr>` +
      ms.map(m => `<tr><td>${esc((m.date || "").slice(0, 19).replace("T", " "))}</td>
        <td class="${m.is_signal ? "up" : "dim"}">${m.is_signal ? "yes (" + esc(m.parsed_by) + ")" : "no"}</td>
        <td class="l">${esc(m.reason)}</td><td class="l"><pre>${esc((m.text || "").slice(0, 300))}</pre></td></tr>`).join("") + `</table>`;
  }catch(e){ document.getElementById("sub").textContent = "disconnected"; }
}
tick(); setInterval(tick, 3000);
</script>
"""


def state(strategy, store):
    s = strategy.status()
    jobs = store.recent_jobs(1000)
    s["stats"] = [stats.summary(jobs, "live"), stats.summary(jobs, "paper")]
    quiet = store.recent_days()
    s["days"] = stats.by_day(jobs, quiet)
    s["no_signal_days"] = len(quiet)
    return s


def make_handler(strategy, store, switch_mode):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _send(self, body, ctype="application/json", code=200):
            data = body if isinstance(body, bytes) else body.encode()
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            try:
                path = urlparse(self.path).path
                if path in ("/", "/index.html"):
                    self._send(PAGE, "text/html; charset=utf-8")
                elif path == "/api/state":
                    self._send(json.dumps(state(strategy, store), default=str))
                else:
                    self._send("{}", code=404)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                self._send(json.dumps({"error": repr(e)[:200]}), code=500)

        def do_POST(self):
            try:
                u = urlparse(self.path)
                if u.path == "/api/mode":
                    to = (parse_qs(u.query).get("to") or [""])[0]
                    self._send(json.dumps(switch_mode(to)))
                else:
                    self._send("{}", code=404)
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception as e:
                self._send(json.dumps({"ok": False, "error": repr(e)[:200]}))
    return Handler


def serve(strategy, store, switch_mode, port=9000, host="0.0.0.0"):
    httpd = ThreadingHTTPServer((host, int(port)), make_handler(strategy, store, switch_mode))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, name="btst-web", daemon=True).start()
    return httpd
