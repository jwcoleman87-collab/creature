"""
web_server.py
=============
Lightweight HTTP server that serves the creature dashboard.
Runs in a background daemon thread — never blocks the main trading loop.

Endpoints:
  GET /           → HTML dashboard (auto-refreshes every 30s)
  GET /api/status → JSON snapshot of all creature state
"""

import os
import json
import threading
from http.server import HTTPServer, BaseHTTPRequestHandler

DASHBOARD_HTML = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>CREATURE — Live Dashboard</title>
<style>
  @import url('https://fonts.googleapis.com/css2?family=Share+Tech+Mono&family=Rajdhani:wght@400;600;700&display=swap');

  :root {
    --bg:       #6b7280;
    --panel:    #4b5563;
    --border:   #374151;
    --green:    #ff8c00;
    --red:      #ff4444;
    --amber:    #ffb800;
    --blue:     #ff8c00;
    --purple:   #ffaa33;
    --dim:      #d1d5db;
    --text:     #ffffff;
    --text-dim: #e5e7eb;
  }

  * { box-sizing: border-box; margin: 0; padding: 0; }

  body {
    background: var(--bg);
    color: var(--text);
    font-family: 'Rajdhani', sans-serif;
    font-size: 15px;
    min-height: 100vh;
    padding: 16px;
  }

  /* ── Header ── */
  .header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 12px 20px;
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    margin-bottom: 16px;
  }
  .logo {
    font-size: 22px;
    font-weight: 700;
    letter-spacing: 4px;
    color: var(--green);
    text-shadow: 0 0 20px rgba(0,255,136,0.4);
  }
  .logo span { color: var(--dim); font-size: 13px; letter-spacing: 2px; }
  .status-pill {
    padding: 4px 14px;
    border-radius: 20px;
    font-size: 12px;
    font-weight: 700;
    letter-spacing: 2px;
    animation: pulse 2s infinite;
  }
  .pill-hunting  { background: rgba(0,255,136,0.15); color: var(--green); border: 1px solid var(--green); }
  .pill-wounded  { background: rgba(255,184,0,0.15);  color: var(--amber); border: 1px solid var(--amber); }
  .pill-lockout  { background: rgba(255,51,85,0.15);  color: var(--red);   border: 1px solid var(--red); }
  .pill-dead     { background: rgba(255,51,85,0.3);   color: var(--red);   border: 1px solid var(--red); }
  .pill-starting { background: rgba(68,136,255,0.15); color: var(--blue);  border: 1px solid var(--blue); }
  @keyframes pulse { 0%,100%{opacity:1} 50%{opacity:0.6} }
  .last-update { color: var(--text-dim); font-size: 12px; font-family: 'Share Tech Mono', monospace; }

  /* ── Grid ── */
  .grid-4 { display: grid; grid-template-columns: repeat(4, 1fr); gap: 12px; margin-bottom: 16px; }
  .grid-3 { display: grid; grid-template-columns: repeat(3, 1fr); gap: 12px; margin-bottom: 16px; }
  .grid-2 { display: grid; grid-template-columns: repeat(2, 1fr); gap: 12px; margin-bottom: 16px; }

  /* ── Cards ── */
  .card {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px 18px;
  }
  .card-label {
    font-size: 11px;
    letter-spacing: 2px;
    color: var(--text-dim);
    text-transform: uppercase;
    margin-bottom: 8px;
  }
  .card-value {
    font-size: 28px;
    font-weight: 700;
    font-family: 'Share Tech Mono', monospace;
    line-height: 1;
  }
  .card-sub { font-size: 12px; color: var(--text-dim); margin-top: 6px; font-family: 'Share Tech Mono', monospace; }
  .green  { color: var(--green); }
  .red    { color: var(--red); }
  .amber  { color: var(--amber); }
  .blue   { color: var(--blue); }
  .purple { color: var(--purple); }
  .dim    { color: var(--dim); }

  /* ── Health bar ── */
  .health-bar-wrap { margin-top: 8px; background: rgba(0,0,0,0.15); border-radius: 4px; height: 4px; }
  .health-bar { height: 4px; border-radius: 4px; transition: width 1s; }

  /* ── The Mind ── */
  .mind-panel {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    margin-bottom: 16px;
    overflow: hidden;
  }
  .mind-header {
    padding: 10px 18px;
    border-bottom: 1px solid var(--border);
    display: flex;
    align-items: center;
    gap: 10px;
    font-size: 11px;
    letter-spacing: 2px;
    color: var(--text-dim);
    text-transform: uppercase;
  }
  .mind-dot { width: 8px; height: 8px; border-radius: 50%; background: var(--green); animation: pulse 1.5s infinite; }
  .mind-log {
    height: 220px;
    overflow-y: auto;
    padding: 12px 18px;
    font-family: 'Share Tech Mono', monospace;
    font-size: 13px;
    display: flex;
    flex-direction: column-reverse;
  }
  .mind-log::-webkit-scrollbar { width: 4px; }
  .mind-log::-webkit-scrollbar-track { background: transparent; }
  .mind-log::-webkit-scrollbar-thumb { background: var(--border); border-radius: 2px; }
  .thought { padding: 3px 0; border-bottom: 1px solid var(--border); }
  .thought .t-time { color: var(--dim); margin-right: 10px; }
  .thought.signal  .t-msg { color: var(--green); }
  .thought.trade   .t-msg { color: var(--amber); }
  .thought.warn    .t-msg { color: var(--red); }
  .thought.error   .t-msg { color: var(--red); }
  .thought.info    .t-msg { color: var(--text); }

  /* ── Open position ── */
  .position-panel {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    padding: 16px 18px;
    margin-bottom: 16px;
  }
  .pos-header {
    font-size: 11px; letter-spacing: 2px; color: var(--text-dim);
    text-transform: uppercase; margin-bottom: 12px;
  }
  .pos-grid { display: grid; grid-template-columns: repeat(5, 1fr); gap: 12px; }
  .pos-item-label { font-size: 11px; color: var(--text-dim); margin-bottom: 4px; }
  .pos-item-value { font-size: 18px; font-weight: 700; font-family: 'Share Tech Mono', monospace; }
  .no-pos { color: var(--dim); font-family: 'Share Tech Mono', monospace; font-size: 14px; }

  /* ── Tables ── */
  .table-panel {
    background: var(--panel);
    border: 1px solid var(--border);
    border-radius: 8px;
    overflow: hidden;
    margin-bottom: 16px;
  }
  .table-header {
    padding: 10px 18px;
    border-bottom: 1px solid var(--border);
    font-size: 11px; letter-spacing: 2px; color: var(--text-dim); text-transform: uppercase;
  }
  table { width: 100%; border-collapse: collapse; }
  th {
    padding: 8px 18px; text-align: left; font-size: 11px;
    letter-spacing: 1px; color: var(--text-dim); border-bottom: 1px solid var(--border);
    font-weight: 400;
  }
  td { padding: 9px 18px; font-family: 'Share Tech Mono', monospace; font-size: 13px; border-bottom: 1px solid var(--border); }
  tr:last-child td { border-bottom: none; }
  .tag {
    display: inline-block; padding: 2px 8px; border-radius: 4px;
    font-size: 11px; font-weight: 700; letter-spacing: 1px;
  }
  .tag-win  { background: rgba(0,255,136,0.15); color: var(--green); }
  .tag-loss { background: rgba(255,51,85,0.15);  color: var(--red); }
  .tag-blocked { background: rgba(255,51,85,0.1); color: var(--red); }
  .tag-boosted { background: rgba(0,255,136,0.1); color: var(--green); }
  .tag-neutral { background: rgba(68,136,255,0.1); color: var(--blue); }

  /* ── Sentiment gauge ── */
  .gauge-wrap { text-align: center; padding: 8px 0; }
  .gauge-value { font-size: 48px; font-weight: 700; font-family: 'Share Tech Mono', monospace; }
  .gauge-label { font-size: 13px; letter-spacing: 2px; margin-top: 4px; }
  .gauge-bar-wrap { margin: 10px 0; height: 8px; border-radius: 4px; background: linear-gradient(to right, var(--green), var(--amber), var(--red)); position: relative; }
  .gauge-marker { position: absolute; top: -4px; width: 16px; height: 16px; border-radius: 50%; background: white; transform: translateX(-50%); transition: left 1s; box-shadow: 0 0 6px rgba(255,255,255,0.5); }
  .gauge-adj { font-size: 13px; font-family: 'Share Tech Mono', monospace; margin-top: 6px; }

  /* ── Bloomberg Ticker ── */
  .ticker-wrap {
    background: #1a1a1a;
    border: 1px solid #ff8c00;
    border-radius: 6px;
    overflow: hidden;
    height: 36px;
    display: flex;
    align-items: center;
    position: sticky;
    top: 0;
    z-index: 1000;
    margin-bottom: 16px;
  }
  .ticker-label {
    background: #ff8c00;
    color: #000;
    font-weight: 700;
    font-size: 11px;
    letter-spacing: 2px;
    padding: 0 12px;
    height: 100%;
    display: flex;
    align-items: center;
    white-space: nowrap;
    flex-shrink: 0;
  }
  .ticker-track {
    display: flex;
    overflow: hidden;
    flex: 1;
  }
  .ticker-inner {
    display: flex;
    animation: ticker-scroll 40s linear infinite;
    white-space: nowrap;
  }
  .ticker-inner:hover { animation-play-state: paused; }
  @keyframes ticker-scroll {
    0%   { transform: translateX(0); }
    100% { transform: translateX(-50%); }
  }
  .ticker-item {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    padding: 0 20px;
    font-family: 'Share Tech Mono', monospace;
    font-size: 12px;
    border-right: 1px solid #333;
  }
  .ticker-sym  { color: #ff8c00; font-weight: 700; }
  .ticker-price { color: #fff; }
  .ticker-up   { color: #00ff88; }
  .ticker-down { color: #ff4444; }
  .ticker-arrow { font-size: 10px; }

  /* ── Footer ── */
  .footer { text-align: center; color: var(--dim); font-size: 11px; letter-spacing: 2px; padding: 12px; }

  /* ── Table scroll on small screens ── */
  .table-panel { overflow-x: auto; }

  @media (max-width: 900px) {
    .grid-4 { grid-template-columns: repeat(2,1fr); }
    .grid-3 { grid-template-columns: repeat(2,1fr); }
    .grid-2 { grid-template-columns: 1fr; }
    .pos-grid { grid-template-columns: repeat(2,1fr); }
  }
  @media (max-width: 480px) {
    body { padding: 10px; }
    .header { flex-wrap: wrap; gap: 8px; }
    .last-update { width: 100%; text-align: center; }
    .grid-4 { grid-template-columns: 1fr 1fr; }
    .grid-3 { grid-template-columns: 1fr; }
    .grid-2 { grid-template-columns: 1fr; }
    .pos-grid { grid-template-columns: repeat(2,1fr); }
    .card-value { font-size: 22px; }
    .gauge-value { font-size: 36px; }
    th, td { padding: 7px 10px; font-size: 12px; }
    .mind-log { font-size: 12px; height: 180px; }
  }
</style>
</head>
<body>

<div class="header">
  <div>
    <div class="logo">🦎 CREATURE <span>/ CRYPTO HUNTER v0.6.0</span></div>
  </div>
  <div id="status-pill" class="status-pill pill-starting">STARTING</div>
  <div class="last-update">Updated: <span id="last-update">—</span></div>
</div>

<!-- Bloomberg Ticker -->
<div class="ticker-wrap">
  <div class="ticker-label">▶ LIVE</div>
  <div class="ticker-track">
    <div class="ticker-inner" id="ticker-inner">
      <div class="ticker-item"><span class="ticker-sym">LOADING</span><span class="ticker-price">—</span></div>
    </div>
  </div>
</div>

<!-- Vitals -->
<div class="grid-4">
  <div class="card">
    <div class="card-label">Account Balance</div>
    <div class="card-value green" id="balance">$—</div>
    <div class="card-sub" id="pnl">— / —%</div>
  </div>
  <div class="card">
    <div class="card-label">Health State</div>
    <div class="card-value" id="health-state">—</div>
    <div class="health-bar-wrap"><div class="health-bar green" id="health-bar" style="width:100%"></div></div>
    <div class="card-sub" id="drawdown">Drawdown: —%</div>
  </div>
  <div class="card">
    <div class="card-label">Learning Phase</div>
    <div class="card-value blue" id="phase">—</div>
    <div class="card-sub" id="total-trades">— total trades</div>
  </div>
  <div class="card">
    <div class="card-label">Today</div>
    <div class="card-value" id="today-pnl">$—</div>
    <div class="card-sub" id="today-trades">— trades today</div>
  </div>
</div>

<!-- Brain metrics -->
<div class="grid-3">
  <div class="card">
    <div class="card-label">Win Rate</div>
    <div class="card-value" id="win-rate">—%</div>
    <div class="card-sub" id="expectancy">Expectancy: — R</div>
  </div>
  <div class="card">
    <div class="card-label">Confidence Score</div>
    <div class="card-value purple" id="confidence">—</div>
    <div class="card-sub">Based on recent performance</div>
  </div>
  <div class="card">
    <div class="card-label">Market Sentiment</div>
    <div class="gauge-wrap">
      <div class="gauge-value" id="fg-value">—</div>
      <div class="gauge-label dim" id="fg-label">—</div>
      <div class="gauge-bar-wrap"><div class="gauge-marker" id="fg-marker" style="left:50%"></div></div>
      <div class="gauge-adj" id="fg-adj">—</div>
    </div>
  </div>
</div>

<!-- The Mind -->
<div class="mind-panel">
  <div class="mind-header">
    <div class="mind-dot"></div>
    THE CREATURE'S MIND — Live Thinking Log
  </div>
  <div class="mind-log" id="mind-log">
    <div class="thought info"><span class="t-time">--:--:--</span><span class="t-msg">Waiting for creature data...</span></div>
  </div>
</div>

<!-- Open Position -->
<div class="position-panel">
  <div class="pos-header">⚡ Open Position</div>
  <div id="position-content">
    <div class="no-pos">No open position — creature is scanning.</div>
  </div>
</div>

<!-- Asset Intelligence + Trade Log -->
<div class="grid-2">
  <div class="table-panel">
    <div class="table-header">🧠 Asset Intelligence (Learned)</div>
    <table>
      <thead><tr><th>Pair</th><th>Score</th><th>Setup</th><th>Regime</th><th>Sentiment</th></tr></thead>
      <tbody id="asset-table"><tr><td colspan="5" class="dim">No signals yet this cycle.</td></tr></tbody>
    </table>
  </div>
  <div class="table-panel">
    <div class="table-header">📋 Recent Trades</div>
    <table>
      <thead><tr><th>Pair</th><th>Setup</th><th>P&L</th><th>R</th><th>Exit</th></tr></thead>
      <tbody id="trade-table"><tr><td colspan="5" class="dim">No trades yet.</td></tr></tbody>
    </table>
  </div>
</div>

<div class="footer">CREATURE / PAPER TRADING / 24-7 / AUTO-REFRESH 30s</div>

<script>
async function refresh() {
  try {
    const res  = await fetch('/api/status');
    const data = await res.json();
    render(data);
  } catch(e) {
    console.warn('Fetch failed:', e);
  }
}

// Show a live clock so the user knows the page is alive
function liveClock() {
  const el = document.getElementById('last-update');
  if (el && el.textContent === '—') {
    el.textContent = 'connecting...';
  }
}
setInterval(liveClock, 1000);

function fmt(n, dec=2) { return n !== null && n !== undefined ? Number(n).toFixed(dec) : '—'; }
function fmtPct(n) { return n !== null && n !== undefined ? Number(n).toFixed(1)+'%' : '—'; }

function render(d) {
  // Header
  const health = d.health?.state || 'STARTING';
  const pill = document.getElementById('status-pill');
  const statusMap = {
    'HEALTHY':'HUNTING','WOUNDED':'WOUNDED','SURVIVAL':'SURVIVAL',
    'LOCKOUT':'LOCKOUT','DEAD':'DEAD','STARTING':'STARTING'
  };
  const classMap = {
    'HEALTHY':'pill-hunting','WOUNDED':'pill-wounded','SURVIVAL':'pill-wounded',
    'LOCKOUT':'pill-lockout','DEAD':'pill-dead','STARTING':'pill-starting'
  };
  pill.textContent = statusMap[health] || health;
  pill.className   = 'status-pill ' + (classMap[health] || 'pill-starting');
  document.getElementById('last-update').textContent =
    d.last_updated ? new Date(d.last_updated).toLocaleTimeString() : '—';

  // Balance
  const bal = d.balance || {};
  document.getElementById('balance').textContent = '$' + fmt(bal.current);
  const pnl = bal.pnl || 0;
  const pnlEl = document.getElementById('pnl');
  pnlEl.textContent = (pnl >= 0 ? '+$' : '-$') + Math.abs(pnl).toFixed(2) + ' / ' + fmt(bal.pnl_pct) + '%';
  pnlEl.className   = 'card-sub ' + (pnl >= 0 ? 'green' : 'red');

  // Health
  const hState = d.health?.state || '—';
  const hEl    = document.getElementById('health-state');
  hEl.textContent = hState;
  hEl.className   = 'card-value ' + ({'HEALTHY':'green','WOUNDED':'amber','SURVIVAL':'amber','LOCKOUT':'red','DEAD':'red'}[hState] || 'dim');
  const dd   = d.health?.drawdown_pct || 0;
  const barW = Math.max(0, 100 - (dd / 10 * 100));
  const bar  = document.getElementById('health-bar');
  bar.style.width = barW + '%';
  bar.className   = 'health-bar ' + (dd < 3 ? 'green' : dd < 5 ? 'amber' : 'red');
  document.getElementById('drawdown').textContent = 'Drawdown: ' + fmt(dd) + '%';

  // Learning
  const lrn = d.learning || {};
  const phaseEl = document.getElementById('phase');
  phaseEl.textContent = (lrn.phase || '—').toUpperCase();
  document.getElementById('total-trades').textContent = (lrn.total_trades || 0) + ' total trades';

  // Today
  const td = d.today || {};
  const tpnl = td.pnl || 0;
  const tEl  = document.getElementById('today-pnl');
  tEl.textContent = (tpnl >= 0 ? '+$' : '-$') + Math.abs(tpnl).toFixed(2);
  tEl.className   = 'card-value ' + (tpnl >= 0 ? 'green' : 'red');
  document.getElementById('today-trades').textContent = (td.trades || 0) + ' trades today';

  // Brain
  const wr  = (lrn.win_rate || 0) * 100;
  const wrEl = document.getElementById('win-rate');
  wrEl.textContent = fmtPct(wr);
  wrEl.className   = 'card-value ' + (wr >= 50 ? 'green' : wr >= 40 ? 'amber' : 'red');
  document.getElementById('expectancy').textContent = 'Expectancy: ' + fmt(lrn.expectancy_r) + ' R';
  const conf = lrn.confidence || 0;
  const confEl = document.getElementById('confidence');
  confEl.textContent = fmt(conf, 1) + ' / 10';

  // Sentiment
  const sent = d.sentiment || {};
  const fgv  = sent.fear_greed;
  document.getElementById('fg-value').textContent = fgv !== null && fgv !== undefined ? fgv : '—';
  const fgLabel = document.getElementById('fg-label');
  fgLabel.textContent = sent.label || '—';
  const fgAdj = sent.adj || 0;
  const adjEl = document.getElementById('fg-adj');
  adjEl.textContent = (fgAdj >= 0 ? 'Signal boost: +' : 'Signal penalty: ') + fmt(Math.abs(fgAdj), 2);
  adjEl.className   = 'gauge-adj ' + (fgAdj > 0 ? 'green' : fgAdj < 0 ? 'red' : 'dim');
  if (fgv !== null && fgv !== undefined) {
    const pct = (100 - fgv);   // fear=left(green), greed=right(red)
    document.getElementById('fg-marker').style.left = pct + '%';
    const fgEl = document.getElementById('fg-value');
    fgEl.className = 'gauge-value ' + (fgv <= 25 ? 'green' : fgv >= 75 ? 'red' : 'amber');
  }

  // Mind log
  const thoughts = (d.thinking || []).slice().reverse();
  const log = document.getElementById('mind-log');
  if (thoughts.length > 0) {
    log.innerHTML = thoughts.map(t =>
      `<div class="thought ${t.level}">` +
      `<span class="t-time">${t.time}</span>` +
      `<span class="t-msg">${t.message}</span></div>`
    ).join('');
  }

  // Open position
  const pos = d.open_position;
  const posDiv = document.getElementById('position-content');
  if (pos) {
    const pnlPos = ((pos.current_price || pos.entry_price) - pos.entry_price) * pos.shares;
    posDiv.innerHTML = `<div class="pos-grid">
      <div><div class="pos-item-label">Pair</div><div class="pos-item-value green">${pos.symbol}</div></div>
      <div><div class="pos-item-label">Entry</div><div class="pos-item-value">$${fmt(pos.entry_price,4)}</div></div>
      <div><div class="pos-item-label">Stop</div><div class="pos-item-value red">$${fmt(pos.stop_price,4)}</div></div>
      <div><div class="pos-item-label">Target</div><div class="pos-item-value green">$${fmt(pos.target_price,4)}</div></div>
      <div><div class="pos-item-label">Shares</div><div class="pos-item-value">${fmt(pos.shares,6)}</div></div>
    </div>
    <div class="card-sub" style="margin-top:10px">Setup: ${pos.setup_type || '—'} &nbsp;|&nbsp; Risk: $${fmt(pos.dollar_risk,4)} &nbsp;|&nbsp; Entered: ${pos.timestamp_entry ? new Date(pos.timestamp_entry).toLocaleTimeString() : '—'}</div>`;
  } else {
    posDiv.innerHTML = '<div class="no-pos">No open position — creature is scanning.</div>';
  }

  // Asset scores
  const assets = d.asset_scores || [];
  const at = document.getElementById('asset-table');
  if (assets.length === 0) {
    at.innerHTML = '<tr><td colspan="5" class="dim">No signals yet this cycle.</td></tr>';
  } else {
    at.innerHTML = assets.map(a => {
      const score = a.score || 0;
      const sentAdj = a.sentiment || 0;
      const sentClass = sentAdj > 0 ? 'green' : sentAdj < 0 ? 'red' : 'dim';
      return `<tr>
        <td class="green">${a.symbol}</td>
        <td class="${score>=5?'green':score>=3?'amber':'dim'}">${fmt(score,2)}</td>
        <td class="dim">${(a.setup||'—').replace('_',' ')}</td>
        <td class="dim">${a.regime||'—'}</td>
        <td class="${sentClass}">${sentAdj>=0?'+':''}${fmt(sentAdj,2)}</td>
      </tr>`;
    }).join('');
  }

  // Recent trades
  const trades = (d.recent_trades || []).slice(0, 10);
  const tt = document.getElementById('trade-table');
  if (trades.length === 0) {
    tt.innerHTML = '<tr><td colspan="5" class="dim">No trades yet.</td></tr>';
  } else {
    tt.innerHTML = trades.map(t => {
      const win = (t.pnl || 0) > 0;
      return `<tr>
        <td class="green">${t.symbol || '—'}</td>
        <td class="dim">${(t.direction||'long').toUpperCase()}</td>
        <td class="${win?'green':'red'}">${win?'+':'-'}$${fmt(Math.abs(t.pnl||0),4)}</td>
        <td class="${win?'green':'red'}">${win?'+':''}${fmt(t.pnl_r||0,2)}R</td>
        <td class="dim">${(t.exit||'—').replace('_',' ')}</td>
      </tr>`;
    }).join('');
  }
}

  // Ticker
  const prices = d.prices || {};
  const syms   = Object.keys(prices);
  if (syms.length > 0) {
    const items = syms.map(sym => {
      const p    = prices[sym];
      const chg  = p.change_pct || 0;
      const dir  = chg >= 0 ? 'up' : 'down';
      const arrow = chg >= 0 ? '▲' : '▼';
      const price = p.price >= 1 ? '$' + Number(p.price).toLocaleString('en-US', {minimumFractionDigits:2, maximumFractionDigits:2})
                                 : '$' + Number(p.price).toFixed(5);
      return `<div class="ticker-item">
        <span class="ticker-sym">${sym.replace('/USD','')}</span>
        <span class="ticker-price">${price}</span>
        <span class="ticker-${dir} ticker-arrow">${arrow} ${Math.abs(chg).toFixed(2)}%</span>
      </div>`;
    }).join('');
    // Duplicate for seamless loop
    const el = document.getElementById('ticker-inner');
    el.innerHTML = items + items;
  }
}

// Initial load + auto-refresh every 10 seconds
refresh();
setInterval(refresh, 10000);
</script>
</body>
</html>"""


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/status":
            from dashboard_state import get_state
            body = json.dumps(get_state(), default=str).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
        elif self.path in ("/", "/index.html"):
            body = DASHBOARD_HTML.encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", len(body))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format, *args):
        pass  # silence access logs — don't pollute creature's output


def start():
    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    print(f"[Dashboard] Live at port {port}")
    return server


if __name__ == "__main__":
    import time
    srv = start()
    print("Press Ctrl+C to stop.")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        srv.shutdown()
        print("Dashboard stopped.")
