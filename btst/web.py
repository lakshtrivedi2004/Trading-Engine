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
  :root{--ink:#202122;--dim:#54595d;--rule:#a2a9b1;--hair:#c8ccd1;--bg:#fff;--panel:#f8f9fa;--surface:#fff;--track:#eaecf0;
        --link:#3366cc;--up:#14866d;--down:#d33;--warn:#b07d00;--upbg:#e8f5f0;--downbg:#fdecec;--warnbg:#fff6dd}
  @media (prefers-color-scheme: dark){:root:not([data-theme="light"]){--ink:#e8e6e3;--dim:#9aa0a6;--rule:#3a3f44;--hair:#33383d;--bg:#14181c;--panel:#1b2025;--surface:#20262b;--track:#2a2f35;--link:#8ab4f8;--up:#3ecf9c;--down:#ff6b6b;--warn:#f0c24a;--upbg:#14302a;--downbg:#2a1616;--warnbg:#2c2614}}
  :root[data-theme="dark"]{--ink:#e8e6e3;--dim:#9aa0a6;--rule:#3a3f44;--hair:#33383d;--bg:#14181c;--panel:#1b2025;--surface:#20262b;--track:#2a2f35;--link:#8ab4f8;--up:#3ecf9c;--down:#ff6b6b;--warn:#f0c24a;--upbg:#14302a;--downbg:#2a1616;--warnbg:#2c2614}
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.55 -apple-system,"Segoe UI",Roboto,"Helvetica Neue",sans-serif;font-variant-numeric:tabular-nums}
  .wrap{max-width:1180px;margin:0 auto;padding:22px 16px 60px}
  h1{font-family:Georgia,"Times New Roman",serif;font-weight:400;font-size:28px;margin:0}
  h2{font-family:Georgia,"Times New Roman",serif;font-weight:400;font-size:20px;margin:30px 0 10px;padding-bottom:4px;border-bottom:1px solid var(--rule)}
  a{color:var(--link)}
  .head{display:flex;justify-content:space-between;align-items:center;gap:12px;flex-wrap:wrap;border-bottom:1px solid var(--rule);padding-bottom:8px}
  .head .right{display:flex;gap:8px;align-items:center}
  .chips{display:flex;flex-wrap:wrap;gap:6px;margin:10px 0 2px;align-items:center}
  .chip{display:inline-flex;align-items:center;gap:5px;border:1px solid var(--hair);background:var(--surface);padding:1px 9px;font-size:12px;border-radius:12px;white-space:nowrap}
  .chip i{width:7px;height:7px;border-radius:50%;background:var(--dim);display:inline-block}
  .chip.ok i{background:var(--up)} .chip.bad i{background:var(--down)} .chip.mid i{background:var(--warn)}
  .chip.live{background:var(--down);border-color:var(--down);color:#fff;font-weight:600}
  .chip.paper{font-weight:600}
  .chip.ce{color:var(--up);border-color:var(--up)} .chip.pe{color:var(--down);border-color:var(--down)}
  .sub{color:var(--dim);font-size:13px;margin:2px 0}
  .btn{border:1px solid var(--hair);background:var(--surface);color:var(--ink);padding:6px 12px;font:inherit;font-size:13px;cursor:pointer;border-radius:2px;text-decoration:none;display:inline-block}
  .btn:hover{background:var(--panel)} .btn:disabled{opacity:.5;cursor:default}
  .btn.danger{border-color:var(--down);color:var(--down)}
  .card{border:1px solid var(--hair);background:var(--panel);padding:14px 16px}
  .cardhead{display:flex;justify-content:space-between;gap:16px;flex-wrap:wrap;align-items:flex-start}
  .sym{font-size:20px;font-weight:600;display:flex;gap:6px;align-items:center;flex-wrap:wrap}
  .hero{text-align:right} .hero .pnl{font-size:30px;line-height:1.1;font-weight:600}
  .n{font-size:12px;color:var(--dim)}
  .phase{margin:12px 0 0;padding:7px 10px;background:var(--surface);border-left:3px solid var(--link);font-size:14px}
  .bar{position:relative;height:10px;background:var(--track);border:1px solid var(--hair);margin:26px 0 22px}
  .bar>b{position:absolute;top:0;bottom:0;left:0}
  .bar .mk{position:absolute;top:-19px;transform:translateX(-50%);font-size:11px;color:var(--dim);white-space:nowrap}
  .bar .tick{position:absolute;top:-4px;bottom:-4px;width:2px;background:var(--ink);transform:translateX(-1px)}
  .bar .lab{position:absolute;top:13px;font-size:11px;color:var(--dim);white-space:nowrap}
  .grid{display:grid;gap:10px;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));margin-top:10px}
  .box{border:1px solid var(--hair);background:var(--surface);padding:9px 11px;min-width:0}
  .k{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--dim)}
  .v{font-size:18px;margin-top:2px;overflow-wrap:anywhere}
  .plan{margin-top:10px;border:1px solid var(--hair);background:var(--surface);padding:9px 11px}
  .plan table{margin:4px 0 0} .plan td{border:0;border-bottom:1px solid var(--hair);padding:4px 6px;text-align:left;white-space:normal}
  .plan tr:last-child td{border-bottom:0}
  .tbl{overflow-x:auto}
  table{border-collapse:collapse;width:100%;font-size:13px}
  th,td{border:1px solid var(--hair);padding:5px 8px;text-align:right;vertical-align:top}
  th{background:var(--panel);text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.04em;color:var(--dim);font-weight:600;white-space:nowrap}
  td:first-child,th:first-child{text-align:left} td.l{text-align:left}
  td .n{display:block;white-space:nowrap}
  tr.quiet td{color:var(--dim);background:var(--panel)}
  tr.det>td{background:var(--panel);text-align:left;padding:8px 10px}
  .det h4{margin:8px 0 3px;font-size:11px;text-transform:uppercase;letter-spacing:.06em;color:var(--dim);font-weight:600}
  .det table{margin:2px 0 6px;font-size:12px} .det td{white-space:normal}
  .up{color:var(--up)} .down{color:var(--down)} .warn{color:var(--warn)} .dim{color:var(--dim)}
  .tag{display:inline-block;font-size:11px;padding:0 6px;border-radius:9px;border:1px solid var(--hair);background:var(--surface);color:var(--dim);margin:1px 2px 1px 0;white-space:nowrap}
  .tag.open{color:var(--up);border-color:var(--up)} .tag.closed{color:var(--ink)} .tag.rej{color:var(--warn);border-color:var(--warn)}
  .tag.win{background:var(--upbg);color:var(--up);border-color:var(--up)} .tag.loss{background:var(--downbg);color:var(--down);border-color:var(--down)}
  .linkbtn{border:0;background:none;color:var(--link);cursor:pointer;font:inherit;font-size:12px;padding:0}
  .modes{display:grid;gap:12px;grid-template-columns:repeat(auto-fit,minmax(320px,1fr))}
  .mode h3{margin:0 0 6px;font-size:14px;font-weight:600}
  .kpis{display:grid;grid-template-columns:repeat(3,1fr);gap:6px}
  .kpis div{border:1px solid var(--hair);background:var(--surface);padding:6px 8px;font-size:12px;color:var(--dim)}
  .kpis b{display:block;font-size:16px;color:var(--ink);font-weight:500}
  .empty{color:var(--dim);font-style:italic;padding:6px 0}
  pre{white-space:pre-wrap;margin:0;font:12px/1.4 ui-monospace,Menlo,monospace;max-height:8.6em;overflow:auto}
  details.msgs summary{cursor:pointer;color:var(--dim);font-size:13px;margin-bottom:6px}
  @media (max-width:640px){.hero{text-align:left}.hero .pnl{font-size:24px}.kpis{grid-template-columns:repeat(2,1fr)}}
</style>
<script>
(function(){
  var KEY = "btst-theme";
  function dark(){ var t = document.documentElement.getAttribute("data-theme");
    return t === "dark" || (t !== "light" && matchMedia("(prefers-color-scheme: dark)").matches); }
  var saved = null; try{ saved = localStorage.getItem(KEY); }catch(e){}
  if (saved === "dark" || saved === "light") document.documentElement.setAttribute("data-theme", saved);
  window.toggleTheme = function(){ var next = dark() ? "light" : "dark";
    try{ localStorage.setItem(KEY, next); }catch(e){}
    document.documentElement.setAttribute("data-theme", next); paintTheme(); };
  window.paintTheme = function(){ var b = document.getElementById("themeBtn"); if (b) b.textContent = dark() ? "☀️" : "🌙"; };
  window.addEventListener("DOMContentLoaded", paintTheme);
})();
</script>
<div class="wrap">
  <div class="head">
    <h1>BTST Desk</h1>
    <div class="right"><span id="ctl"></span><a class="btn" id="logbtn" href="/logs" target="_blank" rel="noopener">View logs</a>
      <button class="btn" id="themeBtn" onclick="toggleTheme()" title="Dark / light">🌙</button></div>
  </div>
  <div class="chips" id="chips"><span class="chip">connecting…</span></div>
  <p class="sub" id="sub"></p>
  <h2>Open trade</h2><div id="open"></div>
  <h2>Trades</h2><div class="tbl" id="days"></div>
  <h2>Statistics</h2><div id="stats"></div>
  <h2>System</h2><div id="sys"></div>
  <h2>Channel messages</h2><div id="msgs"></div>
</div>
<script>
const esc = s => String(s ?? "").replace(/[<>&"]/g, c => ({"<":"&lt;",">":"&gt;","&":"&amp;",'"':"&quot;"}[c]));
const num = x => x != null && x !== "" && !isNaN(x);
const n0 = x => !num(x) ? "–" : Number(x).toLocaleString("en-IN", {maximumFractionDigits: 0});
const n2 = x => !num(x) ? "–" : Number(x).toLocaleString("en-IN", {minimumFractionDigits: 2, maximumFractionDigits: 2});
const cls = x => x > 0 ? "up" : (x < 0 ? "down" : "");
const rs = x => !num(x) ? "–" : (x < 0 ? "−₹" : (x > 0 ? "+₹" : "₹")) + n0(Math.abs(x));
const rsp = x => !num(x) ? "–" : "₹" + n0(x);
const pc = x => !num(x) ? "–" : (x > 0 ? "+" : "") + n2(x) + "%";
const MON = ["Jan","Feb","Mar","Apr","May","Jun","Jul","Aug","Sep","Oct","Nov","Dec"];
const DOW = ["Sun","Mon","Tue","Wed","Thu","Fri","Sat"];
const tm = s => !s ? "–" : `${+s.slice(8, 10)} ${MON[+s.slice(5, 7) - 1] || ""} ${s.slice(11, 16)}`;
const tms = s => !s ? "–" : `${+s.slice(8, 10)} ${MON[+s.slice(5, 7) - 1] || ""} ${s.slice(11, 19)}`;
const day = s => { if (!s) return "–"; const d = new Date(s.slice(0, 10) + "T12:00:00Z"); return `${DOW[d.getUTCDay()]} ${d.getUTCDate()} ${MON[d.getUTCMonth()]}`; };
const SCEN = {hold: "Opened up — target held", hold_small_loss: "Down ≤ 20% — target held",
  average_down: "Down 20–50% — averaged, target avg + 20%", average_down_deep: "Down > 50% — averaged, target avg + 10%, trailing",
  average_down_failed: "Average-down buy failed — target kept", average_down_skipped: "Average down skipped (cancel unconfirmed)",
  closed_on_entry_day: "Closed on the entry day"};
const EXIT = {target: "Target hit", "target (watched)": "Target hit (watched)", time_exit: "15:14 exit", overdue_exit: "Exit at the next open (missed day)"};
const MARKET = {open: ["Market open", "ok"], preopen: ["Pre-open", "mid"], closed: ["Market closed", ""], weekend: ["Weekend", ""], holiday: ["Holiday", ""]};
const OPEN_ROWS = new Set();
let BUSY = false, LAST = null;

async function switchMode(to){
  if (!confirm(to === "live" ? "Switch BTST to LIVE - real orders with real money?" : "Switch BTST back to PAPER?")) return;
  BUSY = true;
  const r = await (await fetch("/api/mode?to=" + to, {method: "POST"})).json().catch(e => ({ok: false, error: String(e)}));
  BUSY = false;
  alert(r.ok ? "BTST is now " + r.mode.toUpperCase() : "Not switched: " + r.error);
}
function toggle(id){ OPEN_ROWS.has(id) ? OPEN_ROWS.delete(id) : OPEN_ROWS.add(id); if (LAST) drawTrades(LAST); }

function drawHead(d){
  const h = d.health || {}, a = h.angel || {}, p = h.price || d.price || {}, live = d.mode === "live";
  const mk = MARKET[d.market] || [d.market || "–", ""];
  const chip = (txt, st, title) => `<span class="chip ${st || ""}" title="${esc(title || "")}"><i></i>${esc(txt)}</span>`;
  const lp = d.log_problems_today || 0;
  document.getElementById("chips").innerHTML =
    `<span class="chip ${live ? "live" : "paper"}">${live ? "LIVE" : "PAPER"}</span>`
    + chip(mk[0], mk[1])
    + chip("Telegram " + (h.reader || "–"), h.reader === "up" ? "ok" : (h.reader ? "bad" : ""), "the signal channel reader")
    + chip("Angel " + (a.last_error ? "error" : (a.logged_in_at ? "logged in " + tm(a.logged_in_at) : "–")), a.last_error ? "bad" : (a.logged_in_at ? "ok" : ""), a.last_error || "")
    + chip("Websocket " + (p.websocket ? "connected" : (p.symbol ? "off" : "idle")), p.websocket ? "ok" : (p.symbol && d.market === "open" ? "bad" : ""), p.symbol || "runs while a trade is open in market hours")
    + (lp ? chip(lp + " warning/error" + (lp > 1 ? "s" : "") + " today", "mid") : "");
  const now = d.now || "";
  document.getElementById("sub").innerHTML = `${day(now)}, ${esc(now.slice(11, 19))} IST · signals read ${esc(d.window)} IST · ${esc(d.note)}`;
  document.getElementById("ctl").innerHTML = d.open_job ? `<span class="n">mode switch after the open trade closes</span>`
    : `<button class="btn ${live ? "" : "danger"}" ${BUSY ? "disabled" : ""} onclick="switchMode('${live ? "paper" : "live"}')">Switch to ${live ? "PAPER" : "LIVE"}</button>`;
  document.getElementById("logbtn").innerHTML = "View logs" + (lp ? ` <span class="warn">(${lp})</span>` : "");
}

function bar(avg, now, target, low, tr){
  if (!num(avg) || !num(target) || target <= avg) return "";
  const lo = Math.min(avg * 0.5, num(low) ? low : avg, num(now) ? now : avg), hi = Math.max(target, num(now) ? now : 0);
  const at = x => Math.max(0, Math.min(100, 100 * (x - lo) / (hi - lo)));
  const a = at(avg), n = num(now) ? at(now) : a;
  const fill = n >= a ? `<b style="left:${a}%;width:${n - a}%;background:var(--up)"></b>` : `<b style="left:${n}%;width:${a - n}%;background:var(--down)"></b>`;
  const edge = x => at(x) > 88 ? "transform:translateX(-100%)" : (at(x) < 12 ? "transform:none" : "");
  const mark = (x, label, cl) => `<span class="tick" style="left:${at(x)}%"></span><span class="mk ${cl || ""}" style="left:${at(x)}%;${edge(x)}">${label}</span>`;
  const floor = tr && num(tr.floor) ? `<span class="lab" style="left:${at(tr.floor)}%;transform:translateX(-50%)">floor ${n2(tr.floor)}</span>` : "";
  return `<div class="bar">${fill}${mark(avg, "avg " + n2(avg))}${mark(target, "target " + n2(target), "up")}`
    + (num(now) ? `<span class="mk ${now >= avg ? "up" : "down"}" style="left:${n}%;top:14px;${edge(now)}">now ${n2(now)}</span>` : "") + floor + `</div>`;
}

function plan(d, j, p){
  const R = d.rules || {}, avg = p.avg_price, t = j.target_price;
  if (j.decision){
    const dec = j.decision, tr = j.trail;
    return `<div class="plan"><div class="k">Today's plan</div><table>
      <tr><td>Decision ${tms(dec.at)}</td><td>${esc(SCEN[dec.scenario] || dec.scenario)} (opened ${pc(dec.pnl_pct)} at ${n2(dec.ltp)})</td></tr>
      ${dec.added_qty ? `<tr><td>Averaged</td><td>+${n0(dec.added_qty)} @ ${n2(dec.added_price)} (${rsp(dec.added_amount)}) → ${n0(p.qty)} @ ${n2(avg)}</td></tr>` : ""}
      ${tr ? `<tr><td>Trailing</td><td>${tr.active ? "on" : "stopped"}: from ${n2(tr.initial)}, 1 point per point below ${n2(tr.ref)} (lowest ${n2(tr.low)}), floor ${n2(tr.floor)} · ${(tr.steps || []).length} step(s)${tr.stop_reason ? " · " + esc(tr.stop_reason) : ""}</td></tr>` : ""}
      <tr><td>Target</td><td>${n2(t)} — sells the whole position when reached</td></tr>
      <tr><td>Exit</td><td>at market at ${esc(R.exit_at)} if the target is not hit</td></tr></table></div>`;
  }
  const hold = avg * (1 - (R.avg_down_loss_pct || 20) / 100), deep = avg * (1 - (R.deep_loss_pct || 50) / 100);
  return `<div class="plan"><div class="k">Next-day plan — ${day(d.next_session)} ${esc(R.decide_at)}</div><table>
    <tr><td>Price ≥ ${n2(hold)}</td><td>(up, or down ≤ ${R.avg_down_loss_pct}%) keep the target ${n2(t)}</td></tr>
    <tr><td>${n2(deep)} – ${n2(hold)}</td><td>(down ${R.avg_down_loss_pct}–${R.deep_loss_pct}%) buy ≈${rsp(p.invested)} more, target = new average + ${R.avg_target_pct}%</td></tr>
    <tr><td>Below ${n2(deep)}</td><td>(down > ${R.deep_loss_pct}%) buy ≈${rsp(p.invested)} more, target = new average + ${R.deep_target_pct}%, trailed ${R.trail_step_points} point per point, floor average + ${R.trail_floor_points}</td></tr>
    <tr><td>Exit</td><td>at market at ${esc(R.exit_at)} that day if the target is not hit</td></tr></table></div>`;
}

function drawOpen(d){
  const j = d.open_job, el = document.getElementById("open");
  if (!j){ el.innerHTML = `<p class="empty">No trade open. Signals are read ${esc(d.window)} IST.</p>`; return; }
  const r = (d.days || []).find(x => x.job_id === j.job_id) || {}, p = j.position || {}, c = j.contract || {}, s = j.signal || {};
  const R = d.rules || {}, closed = /closed/.test(j.last_ltp_source || ""), today = (d.now || "").slice(0, 10);
  const tKind = j.soft_target ? "watched (order refused)" : (j.target_order && j.target_order.kind === "amo_target" ? "AMO at Angel" : "limit order at Angel");
  const away = num(r.now_price) && num(j.target_price) ? 100 * (j.target_price - r.now_price) / r.now_price : null;
  const phaseTxt = j.trade_date === today && !j.decision
    ? `Entry day — the target is at Angel until ${esc(R.market_close)}` + (j.mode === "live" ? `; it goes back in as an AMO at ${esc(R.amo_at)}` : "")
    : (!j.decision ? `Waiting for the next session — decision ${day(d.next_session)} at ${esc(R.decide_at)}` : `${esc(SCEN[j.decision.scenario] || j.decision.scenario)} — out at ${esc(R.exit_at)} if the target is not hit`);
  const box = (k, v, n) => `<div class="box"><div class="k">${k}</div><div class="v">${v}</div><div class="n">${n || ""}</div></div>`;
  const amoTxt = j.amo ? `${esc(j.amo.order_id)} · ${n2(j.amo.price)} x${n0(j.amo.qty)}` : "–";
  const amoNote = j.amo ? `placed ${tm(j.amo.placed_at)}${(j.amo_history || []).length ? " · last check " + esc(j.amo_history.slice(-1)[0].slot) + " " + esc(j.amo_history.slice(-1)[0].action) : ""}${j.target_order && j.target_order.kind !== "amo_target" ? " · replaced since" : ""}`
    : (j.mode === "live" ? `placed at ${esc(R.amo_at)} while the trade waits` : "live trades only");
  el.innerHTML = `<div class="card">
    <div class="cardhead">
      <div>
        <div class="sym">${esc(s.indices)} ${esc(s.strike_price)} <span class="chip ${s.side === "CE" ? "ce" : "pe"}">${esc(s.side)}</span>
          <span class="chip ${j.mode === "live" ? "live" : "paper"}">${esc((j.mode || "").toUpperCase())}</span></div>
        <div class="n">${esc(c.symbol)} · ${esc(c.exchange)} · expiry ${esc(c.expiry)} · lot ${esc(c.lot_size)} · ${esc(j.job_id)}</div>
      </div>
      <div class="hero"><div class="pnl ${cls(r.live_pnl)}">${rs(r.live_pnl)}</div>
        <div class="n ${cls(r.live_pct)}">${pc(r.live_pct)} on ${rsp(p.invested)}</div>
        <div class="n">${closed ? "last traded" : "now"} ${n2(r.now_price)} · ${closed ? "market closed · " : ""}${tm(j.last_ltp_at)}</div></div>
    </div>
    <div class="phase">${phaseTxt}</div>
    ${bar(p.avg_price, r.now_price, j.target_price, j.ltp_low, j.trail)}
    <div class="grid">
      ${box("Bought", `${n0(p.entry_qty)} @ ${n2(p.entry_price)}`, `${tm(p.entry_at)} · ${n0(j.lots)} lot(s) = ${rsp(p.entry_qty * p.entry_price)}${R.trade_amount ? " (cap " + rsp(R.trade_amount * (1 + (R.amount_tolerance_pct || 0) / 100)) + ")" : ""}`)}
      ${box("Average · invested", `${n2(p.avg_price)} · ${rsp(p.invested)}`, p.added_qty ? `+${n0(p.added_qty)} @ ${n2(p.added_price)} ${tm(p.added_at)} → ${n0(p.qty)} qty` : `${n0(p.qty)} qty`)}
      ${box("Target", n2(j.target_price), `${tKind} · ${esc(j.target_last_status || "–")}${num(away) ? " · " + pc(away) + " away" : ""}`)}
      ${box("Signal", `@ ${esc(s.price)}`, `posted ${tm((j.message || {}).date)} · read by ${esc(s.parsed_by)}`)}
      ${box("High / low since entry", `${n2(j.ltp_high)} / ${n2(j.ltp_low)}`, `${pc(p.avg_price ? 100 * (j.ltp_high - p.avg_price) / p.avg_price : null)} / ${pc(p.avg_price ? 100 * (j.ltp_low - p.avg_price) / p.avg_price : null)}`)}
      ${j.mode === "live" ? box("AMO", amoTxt, amoNote) : ""}
    </div>
    ${plan(d, j, p)}
  </div>`;
}

function detail(r){
  const t = r.timeline; if (!t) return `<p class="empty">Older trade — see the log.</p>`;
  const tbl = (head, rows) => rows.length ? `<table><tr>${head.map(h => `<th>${h}</th>`).join("")}</tr>${rows.join("")}</table>` : `<p class="empty">none</p>`;
  return `<h4>Timeline</h4>` + tbl(["Time", "What happened"], t.events.map(e => `<tr><td>${tms(e.at)}</td><td class="l">${esc(e.text)}</td></tr>`))
    + `<h4>Target history</h4>` + tbl(["Time", "Kind", "Price", "From", "Order", "Status", "Why"], t.targets.map(h => `<tr><td>${tms(h.at)}</td><td class="l">${esc(h.kind)}</td><td>${n2(h.price)}</td><td>${n2(h.previous)}</td><td class="l">${esc(h.order_id || (h.watched ? "watched" : ""))}</td><td class="l">${esc(h.status || (h.ok === false ? "refused" : ""))}</td><td class="l">${esc(h.why || h.error || "")}</td></tr>`))
    + `<h4>AMO checks</h4>` + tbl(["Time", "For", "Action", "Order", "Status", "Why / error"], t.amo.map(a => `<tr><td>${tms(a.at)}</td><td class="l">${esc(a.slot)}</td><td class="l">${esc(a.action)}</td><td class="l">${esc(a.order_id || "")}</td><td class="l">${esc(a.status || "")}</td><td class="l">${esc(a.why || a.error || "")}</td></tr>`))
    + `<h4>Orders</h4>` + tbl(["Time", "Kind", "Side", "Qty", "Price", "Filled", "Status", "Order", "Error"], t.orders.map(o => `<tr><td>${tms(o.at)}</td><td class="l">${esc(o.kind)}${o.variety === "AMO" ? ' <span class="tag">AMO</span>' : ""}</td><td class="l">${esc(o.side || "")}</td><td>${n0(o.qty)}</td><td>${n2(o.price_sent)}</td><td>${o.filled ? n0(o.filled) + " @ " + n2(o.avg_price) : ""}</td><td class="l">${esc(o.status || "")}</td><td class="l">${esc(o.order_id || "")}</td><td class="l">${esc(o.error || "")}</td></tr>`));
}

function drawTrades(d){
  const rows = d.days || [], COLS = 10;
  if (!rows.length){ document.getElementById("days").innerHTML = `<p class="empty">No BTST yet.</p>`; return; }
  document.getElementById("days").innerHTML = `<table><tr><th>Date</th><th>Trade</th><th>Bought</th><th>Avg · invested</th>
      <th>Now / exit</th><th>P&amp;L</th><th>Target</th><th>Next day</th><th>Result</th><th></th></tr>` +
    rows.map(r => {
      if (!r.job_id) return `<tr class="quiet"><td>${day(r.date)}</td><td class="l" colspan="${COLS - 1}">No signal — ${n0(r.messages_read)} message(s) read in the scan window · no trade</td></tr>`;
      const open = r.status === "open", rej = r.status !== "open" && r.status !== "closed";
      const result = open ? `<span class="tag open">open</span>`
        : rej ? `<span class="tag rej">${esc(r.status)}</span><div class="n" style="white-space:normal">${esc(r.reason)}</div>`
        : `<span class="tag ${r.net_pnl > 0 ? "win" : "loss"}">${r.net_pnl > 0 ? "win" : "loss"}</span> ${esc(EXIT[r.exit_reason] || r.exit_reason || "")}`;
      const nowNote = open ? (/closed/.test(r.now_source || "") ? "last traded · " + tm(r.now_at) : "live") : tm(r.exit_at);
      const main = `<tr><td>${day(r.date)}<span class="n">${esc(r.mode)}</span></td>
        <td class="l"><b>${esc(r.signal || "–")}</b><span class="n">${esc(r.symbol || "")}${r.signal_at ? " · signal " + tm(r.signal_at) : ""}</span></td>
        <td>${r.qty ? n0(r.entry_qty || r.qty) + " @ " + n2(r.entry_price) : "–"}<span class="n">${tm(r.buy_at)}</span></td>
        <td>${n2(r.avg_price)}<span class="n">${rsp(r.invested)}${r.added_qty ? " · +" + n0(r.added_qty) + " @ " + n2(r.added_price) : ""}</span></td>
        <td>${n2(r.now_price)}<span class="n">${nowNote}</span></td>
        <td class="${cls(r.live_pnl)}">${rs(r.live_pnl)}<span class="n ${cls(r.live_pct)}">${pc(r.live_pct)}</span></td>
        <td>${n2(r.target)}<span class="n">${r.target_kind === "amo_target" ? "AMO" : (r.watched ? "watched" : (r.target ? "limit" : ""))}${r.trail ? " · trail " + r.trail.steps : ""}</span></td>
        <td class="l">${esc(SCEN[r.scenario] || r.scenario || (open ? "pending" : ""))}${num(r.opened_pct) ? `<span class="n">opened ${pc(r.opened_pct)}</span>` : ""}</td>
        <td class="l">${result}</td>
        <td><button class="linkbtn" onclick="toggle('${esc(r.job_id)}')">${OPEN_ROWS.has(r.job_id) ? "hide" : "details"}</button><span class="n"><a href="/logs?day=${encodeURIComponent(r.date || "")}" target="_blank" rel="noopener">log</a></span></td></tr>`;
      return main + (OPEN_ROWS.has(r.job_id) ? `<tr class="det"><td colspan="${COLS}">${detail(r)}</td></tr>` : "");
    }).join("") + `</table>`;
}

function drawStats(d){
  const mode = s => {
    if (!s.trades) return `<div class="box mode"><h3>${esc(s.mode.toUpperCase())}</h3><p class="empty">No closed trades yet${s.open_or_failed ? ` · ${s.open_or_failed} open / not traded` : ""}.</p></div>`;
    const k = (label, v, c) => `<div>${label}<b class="${c || ""}">${v}</b></div>`;
    return `<div class="box mode"><h3>${esc(s.mode.toUpperCase())} · ${s.trades} closed trade(s)</h3><div class="kpis">
      ${k("Net P&L", rs(s.net_pnl), cls(s.net_pnl))}${k("Win rate", n2(s.win_rate) + "%")}${k("Wins / losses", s.wins + " / " + s.losses)}
      ${k("Avg per trade", rs(s.avg_pnl), cls(s.avg_pnl))}${k("Avg % of invested", pc(s.avg_pct), cls(s.avg_pct))}${k("Profit factor", s.profit_factor == null ? "–" : n2(s.profit_factor))}
      ${k("Best / worst", rs(s.best) + " / " + rs(s.worst))}${k("Max drawdown", rsp(s.max_drawdown))}${k("Charges", rsp(s.charges))}
      ${k("Target / timed exit", s.target_hits + " / " + s.time_exits)}${k("Averaged / trailed", s.averaged + " / " + (s.trailed || 0))}${k("Invested (sum)", rsp(s.invested_total))}
    </div></div>`;
  };
  document.getElementById("stats").innerHTML = `<div class="modes">${(d.stats || []).map(mode).join("")}</div>
    <p class="sub">Trading days with no signal: ${d.no_signal_days || 0}</p>`;
}

function drawSys(d){
  const h = d.health || {}, a = h.angel || {}, p = h.price || d.price || {}, ho = h.holidays || {}, R = d.rules || {};
  const box = (k, v, n) => `<div class="box"><div class="k">${k}</div><div class="v" style="font-size:15px">${v}</div><div class="n">${n || ""}</div></div>`;
  document.getElementById("sys").innerHTML = `<div class="grid">
    ${box("Angel session", a.logged_in_at ? "logged in " + tm(a.logged_in_at) : "–", `${a.logins || 0} login(s) · fresh login daily at ${esc(R.session_refresh_at || "–")}${a.auth_refreshes ? " · " + a.auth_refreshes + " token re-login(s)" : ""}${a.last_error ? " · last error: " + esc(a.last_error) : ""}`)}
    ${box("Live price", p.websocket ? "websocket" : (p.symbol ? "LTP call" : "idle"), p.symbol ? `${esc(p.symbol)} · ${n0(p.ticks)} ticks · ${n0(p.ltp_calls)} LTP calls${p.ws_restarts ? " · " + p.ws_restarts + " restart(s)" : ""}` : "the websocket runs while a trade is open in market hours")}
    ${box("Telegram reader", h.reader || "–", "reads the signal channel (set in .angel.env)")}
    ${box("Holidays", `${ho.known_this_year ?? "–"} this year`, `${esc(ho.source || "")}${(ho.next || []).length ? " · next " + ho.next.map(day).join(", ") : ""}${ho.error ? " · fetch error: " + esc(ho.error) : ""}`)}
    ${box("Sizing", R.trade_amount ? rsp(R.trade_amount) + " ± " + n0(R.amount_tolerance_pct) + "%" : "fixed lots", R.trade_amount ? "the most lots below " + rsp(R.trade_amount * (1 + (R.amount_tolerance_pct || 0) / 100)) + " · no compounding" : "")}
  </div>`;
}

function drawMsgs(d){
  const ms = d.messages || [], prev = document.querySelector("details.msgs");
  const wasOpen = prev ? prev.open : true;
  document.getElementById("msgs").innerHTML = !ms.length ? `<p class="empty">Nothing read yet.</p>` :
    `<details class="msgs" ${wasOpen ? "open" : ""}><summary>The last ${ms.length} messages read (${esc(d.window)} IST), newest first</summary><div class="tbl"><table><tr><th>Posted</th><th>Signal?</th><th>Why</th><th>Message</th></tr>` +
    ms.map(m => `<tr><td>${tms(m.date)}</td>
      <td class="l ${m.is_signal ? "up" : "dim"}">${m.is_signal ? "yes (" + esc(m.parsed_by) + ")" : "no"}</td>
      <td class="l">${esc(m.reason)}</td><td class="l"><pre>${esc((m.text || "").slice(0, 300))}</pre></td></tr>`).join("") + `</table></div></details>`;
}

async function tick(){
  try{
    const d = await (await fetch("/api/state")).json();
    LAST = d;
    drawHead(d); drawOpen(d); drawTrades(d); drawStats(d); drawSys(d); drawMsgs(d);
  }catch(e){ document.getElementById("chips").innerHTML = `<span class="chip bad"><i></i>disconnected</span>`; }
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
  read in the scan window and why it was or was not a signal, then why a signal was not traded. <i>Problems</i> shows the warnings
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
    s["no_signal_days"] = sum(1 for r in s["days"] if not r.get("job_id"))
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
