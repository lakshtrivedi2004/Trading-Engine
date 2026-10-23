"""The BTST page and its JSON, on its own port (config/btst.json "port", 9000).

    GET  /             the page: mode, the open trade, every day's trade, the
                       statistics per mode, the last channel messages read
    GET  /api/state    the same as JSON
    GET  /logs         the desk's log (btst/logbook.py): every message read, why
                       it was or was not a signal, every order, every decision,
                       every error - filtered by kind, day and text
    GET  /api/logs?limit=&group=errors|trades|signals|ai&q=&day=YYYY-MM-DD
    POST /api/mode?to=paper|live   switch mode - refused while a trade is open
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from btst import stats
from btst.logbook import GROUPS
from arb.instruments import now_ist

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
  a{color:inherit} .head{display:flex;justify-content:space-between;align-items:flex-end;gap:10px;flex-wrap:wrap;border-bottom:1px solid var(--rule);padding-bottom:6px;margin-bottom:4px}
  .head h1{border:0;padding:0;margin:0} a.btn{text-decoration:none;display:inline-block}
</style>
<div class="wrap">
  <div class="head"><h1>BTST Desk</h1><a class="btn" id="logbtn" href="/logs" target="_blank" rel="noopener">View logs</a></div>
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
    const lp = d.log_problems_today || 0;
    document.getElementById("logbtn").innerHTML = "View logs" + (lp ? ` <span class="warn">· ${lp} warning/error${lp > 1 ? "s" : ""} today</span>` : "");
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
        <div class="box"><div class="k">LTP / open P&L</div><div class="v ${cls(pnl)}">${n2(j.last_ltp)} · ${rs(pnl)}</div><div class="dim">at ${esc(j.last_ltp_at || "")}${d.price && d.price.source ? " · via " + esc(d.price.source) + (d.price.websocket ? " (" + n0(d.price.ticks) + " ticks)" : "") : ""}${d.price && d.price.peak_since_target ? " · highest since target " + n2(d.price.peak_since_target) : ""}</div></div>
        <div class="box"><div class="k">Next-day decision</div><div class="v" style="font-size:16px">${esc(dec.scenario || "at the open")}</div><div class="dim">${dec.pnl_pct != null ? "opened " + n2(dec.pnl_pct) + "%" : ""}</div></div>
      </div><p class="sub">${esc(j.job_id)} · signal: ${esc((j.signal||{}).indices)} ${esc((j.signal||{}).strike_price)} ${esc((j.signal||{}).side)} @ ${esc((j.signal||{}).price)}</p>`; })();
    document.getElementById("stats").innerHTML = (d.stats || []).map(statBlock).join("")
      + `<p class="sub">Trading days with no signal (no trade): ${d.no_signal_days || 0}</p>`;
    const rows = d.days || [];
    document.getElementById("days").innerHTML = !rows.length ? `<p class="dim">No BTST yet.</p>` :
      `<table><tr><th>Date</th><th>Mode</th><th>Signal</th><th>Contract</th><th>Qty</th><th>Avg</th><th>Invested</th><th>Target</th>
       <th>Next day</th><th>Exit</th><th>Exit price</th><th>Net</th><th>%</th><th>Status</th><th>Log</th></tr>` +
      rows.map(r => `<tr class="${r.job_id ? "" : "dim"}"><td>${esc(r.date)}</td><td>${esc(r.mode)}</td><td class="l">${esc(r.signal)}</td><td>${esc(r.symbol || "–")}</td>
        <td>${n0(r.qty)}</td><td>${n2(r.avg_price)}</td><td>${rs(r.invested)}</td><td>${n2(r.target)}</td><td>${esc(r.scenario || "")}</td>
        <td>${esc(r.exit_reason || "")}</td><td>${n2(r.exit_price)}</td><td class="${cls(r.net_pnl)}">${rs(r.net_pnl)}</td>
        <td class="${cls(r.pnl_pct)}">${r.pnl_pct == null ? "–" : n2(r.pnl_pct) + "%"}</td>
        <td class="l">${esc(r.status)}${r.reason ? " – " + esc(r.reason) : ""}</td>
        <td><a href="/logs?day=${encodeURIComponent(r.date || "")}" target="_blank" rel="noopener">log</a></td></tr>`).join("") + `</table>`;
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


LOGS_PAGE = r"""<!DOCTYPE html>
<meta charset="utf-8">
<title>BTST Log</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
  :root{--ink:#202122;--dim:#54595d;--rule:#a2a9b1;--hair:#c8ccd1;--bg:#fff;--panel:#f8f9fa;--up:#14866d;--down:#d33;--warn:#b07d00;--trade:#3366cc}
  @media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--ink:#e8e6e3;--dim:#9aa0a6;--rule:#3a3f44;--hair:#33383d;--bg:#14181c;--panel:#1b2025;--up:#3ecf9c;--down:#ff6b6b;--warn:#f0c24a;--trade:#7aa7ff}}
  :root[data-theme="dark"]{--ink:#e8e6e3;--dim:#9aa0a6;--rule:#3a3f44;--hair:#33383d;--bg:#14181c;--panel:#1b2025;--up:#3ecf9c;--down:#ff6b6b;--warn:#f0c24a;--trade:#7aa7ff}
  *{box-sizing:border-box} body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 -apple-system,"Segoe UI",Roboto,sans-serif}
  .wrap{max-width:1280px;margin:0 auto;padding:18px 16px 60px}
  h1{font-family:Georgia,serif;font-weight:400;font-size:26px;margin:0}
  .head{display:flex;justify-content:space-between;align-items:flex-end;gap:10px;flex-wrap:wrap;border-bottom:1px solid var(--rule);padding-bottom:6px}
  .sub{color:var(--dim);font-size:13px;margin:6px 0}
  .bar{display:flex;flex-wrap:wrap;gap:8px;align-items:center;margin:10px 0;position:sticky;top:0;background:var(--bg);padding:6px 0;border-bottom:1px solid var(--hair)}
  .btn,input,select{border:1px solid var(--hair);background:var(--panel);color:var(--ink);padding:5px 10px;font:inherit;font-size:13px}
  .btn{cursor:pointer;text-decoration:none} .btn.on{background:var(--ink);color:var(--bg);border-color:var(--ink)}
  input[type=search]{min-width:200px;flex:1}
  .row{display:grid;grid-template-columns:150px 54px 78px 1fr;gap:8px;padding:3px 4px;border-bottom:1px solid var(--hair);font:12.5px/1.45 ui-monospace,Menlo,monospace}
  .row:hover{background:var(--panel)} .t{color:var(--dim)} .a{color:var(--dim);overflow:hidden;text-overflow:ellipsis}
  .x{white-space:pre-wrap;word-break:break-word}
  .ERROR .lv,.ERROR .x{color:var(--down)} .WARN .lv{color:var(--warn)} .TRADE .lv{color:var(--trade)} .INFO .lv{color:var(--dim)}
  .ERROR{background:color-mix(in srgb,var(--down) 7%,transparent)} .TRADE .x{font-weight:600}
  .empty{color:var(--dim);padding:20px 0}
  @media (max-width:640px){.row{grid-template-columns:1fr;gap:0;padding:6px 4px}.row .t:after{content:""}}
</style>
<div class="wrap">
  <div class="head"><h1>BTST Log</h1><a class="btn" href="/">← BTST Desk</a></div>
  <p class="sub">Every step of the desk, newest first. <b>No trade today?</b> Pick <i>Signals</i>: each channel message
  read 15:15 – 15:30 and why it was or was not a signal, then why a signal was not traded. <i>Problems</i> shows the warnings
  and errors (Telegram reader down, Angel login, refused orders).</p>
  <div class="bar">
    <span id="groups"></span>
    <input type="search" id="q" placeholder="search (job id, symbol, word…)">
    <input type="date" id="day" title="only this day">
    <select id="limit"><option>200</option><option selected>500</option><option>1000</option><option>3000</option></select>
    <label class="sub"><input type="checkbox" id="auto" checked> auto-refresh</label>
    <button class="btn" onclick="load()">Refresh</button>
  </div>
  <p class="sub" id="meta">loading…</p>
  <div id="rows"></div>
</div>
<script>
const esc = s => String(s ?? "").replace(/[<>&]/g, c => ({"<":"&lt;",">":"&gt;","&":"&amp;"}[c]));
const GROUPS = [["", "All"], ["errors", "Problems"], ["signals", "Signals"], ["trades", "Trades"], ["ai", "AI"]];
const P = new URLSearchParams(location.search);
let group = P.get("group") || "";
document.getElementById("q").value = P.get("q") || "";
document.getElementById("day").value = P.get("day") || "";
if (P.get("limit")) document.getElementById("limit").value = P.get("limit");
function drawGroups(){
  document.getElementById("groups").innerHTML = GROUPS.map(([g, label]) =>
    `<button class="btn ${g === group ? "on" : ""}" onclick="group='${g}';load()">${label}</button>`).join(" ");
}
let seq = 0;
async function load(){
  drawGroups();
  const q = document.getElementById("q").value.trim(), day = document.getElementById("day").value,
        limit = document.getElementById("limit").value;
  const u = new URLSearchParams();
  if (group) u.set("group", group); if (q) u.set("q", q); if (day) u.set("day", day); u.set("limit", limit);
  history.replaceState(null, "", "/logs?" + u.toString());
  const my = ++seq;
  try{
    const d = await (await fetch("/api/logs?" + u.toString())).json();
    if (my !== seq) return;
    if (d.error) throw new Error(d.error);
    const c = d.counts || {};
    document.getElementById("meta").textContent = `${d.lines.length} line(s) from ${d.source}` +
      (day ? ` on ${day}` : "") + ` · in view: ${c.ERROR || 0} error(s), ${c.WARN || 0} warning(s), ${c.TRADE || 0} trade line(s) · ${d.now} IST`;
    document.getElementById("rows").innerHTML = !d.lines.length ? `<p class="empty">Nothing logged for this filter.</p>` :
      d.lines.map(r => `<div class="row ${esc(r.level)}"><span class="t">${esc(r.at || "–")}</span><span class="lv">${esc(r.level)}</span>
        <span class="a" title="${esc(r.area)}">${esc(r.area)}</span><span class="x">${esc(r.text)}</span></div>`).join("");
  }catch(e){ document.getElementById("meta").textContent = "could not load the log: " + e; }
}
let typing;
document.getElementById("q").addEventListener("input", () => { clearTimeout(typing); typing = setTimeout(load, 300); });
for (const id of ["day", "limit"]) document.getElementById(id).addEventListener("change", load);
setInterval(() => { if (document.getElementById("auto").checked && !getSelection().toString()) load(); }, 5000);
load();
</script>
"""


def state(strategy, store, book=None):
    s = strategy.status()
    jobs = store.recent_jobs(1000)
    s["stats"] = [stats.summary(jobs, "live"), stats.summary(jobs, "paper")]
    quiet = store.recent_days()
    s["days"] = stats.by_day(jobs, quiet)
    s["no_signal_days"] = len(quiet)
    if book is not None:
        try:
            s["log_problems_today"] = len(book.lines(limit=500, group="errors", scan=4000,
                                                     day=now_ist().date().isoformat())[0])
        except Exception:
            s["log_problems_today"] = None
    return s


def logs(book, query):
    """/api/logs: the log after the page's filters, newest first."""
    one = lambda k, d="": (query.get(k) or [d])[0].strip()
    try:
        limit = max(1, min(5000, int(one("limit", "500"))))
    except ValueError:
        limit = 500
    group = one("group")
    rows, source = book.lines(limit=limit, group=group if group in GROUPS else None,
                              q=one("q") or None, day=one("day") or None)
    counts = {}
    for r in rows:
        counts[r["level"]] = counts.get(r["level"], 0) + 1
    return {"now": now_ist().isoformat(timespec="seconds"), "source": source, "counts": counts, "lines": rows}


def make_handler(strategy, store, switch_mode, book=None):
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
                    self._send(json.dumps(state(strategy, store, book), default=str))
                elif path == "/logs":
                    self._send(LOGS_PAGE, "text/html; charset=utf-8")
                elif path == "/api/logs":
                    if book is None:
                        self._send(json.dumps({"error": "no log on this desk"}), code=404)
                    else:
                        self._send(json.dumps(logs(book, parse_qs(urlparse(self.path).query)), default=str))
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


def serve(strategy, store, switch_mode, port=9000, host="0.0.0.0", book=None):
    httpd = ThreadingHTTPServer((host, int(port)), make_handler(strategy, store, switch_mode, book))
    httpd.daemon_threads = True
    threading.Thread(target=httpd.serve_forever, name="btst-web", daemon=True).start()
    return httpd
