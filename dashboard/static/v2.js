/* Nova Fleet dashboard v2 — served at /v2, alongside the existing UI.
 *
 * Reads the SAME read-only endpoints the current dashboard uses; no new backend
 * surface and no new credentials. Every number shown here comes from Alpaca via
 * the server, not from anything computed in the browser, so the page cannot
 * disagree with the broker about money.
 */
'use strict';

const ACCOUNTS = [
  { id: 'prod',     label: 'Main' },
  { id: 'trading2', label: 'Nova' },
  { id: 'sofi',     label: 'Sofi' },
];

const state = { summaries: {}, positions: {}, trades: {}, issues: [], chartAcct: 'prod', equity: {} };

/* ── helpers ────────────────────────────────────────────────────────────── */
const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));
const el = (t, c, txt) => { const n = document.createElement(t); if (c) n.className = c; if (txt != null) n.textContent = txt; return n; };

const num = v => (v == null || v === '' || isNaN(Number(v))) ? null : Number(v);
const money = v => { const n = num(v); return n == null ? '—' :
  n.toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: n >= 1000 ? 0 : 2 }); };
const money2 = v => { const n = num(v); return n == null ? '—' :
  n.toLocaleString(undefined, { style: 'currency', currency: 'USD', minimumFractionDigits: 2, maximumFractionDigits: 2 }); };
const pct = v => { const n = num(v); return n == null ? '—' : (n >= 0 ? '+' : '') + n.toFixed(2) + '%'; };
const signed = v => { const n = num(v); return n == null ? '—' : (n >= 0 ? '+' : '') + money2(Math.abs(n)).replace('$', n < 0 ? '-$' : '$'); };
const cls = v => { const n = num(v); return n == null ? '' : n > 0 ? 'up' : n < 0 ? 'down' : ''; };

async function api(path) {
  const res = await fetch(path, { credentials: 'same-origin' });
  if (res.status === 401) { showLogin(); throw new Error('unauthenticated'); }
  if (!res.ok) throw new Error(path + ' → ' + res.status);
  return res.json();
}

function showLogin(msg) {
  $('#app').classList.add('hidden');
  $('#login').classList.remove('hidden');
  $('#login-error').textContent = msg || '';
}
function showApp() {
  $('#login').classList.add('hidden');
  $('#app').classList.remove('hidden');
}

/* ── tiles ──────────────────────────────────────────────────────────────── */
function renderTiles() {
  const host = $('#tiles');
  host.innerHTML = '';
  for (const acct of ACCOUNTS) {
    const s = state.summaries[acct.id];
    const tile = el('div', 'tile');

    const equity = s ? num(s.equity) : null;
    const last = s ? num(s.last_equity) : null;
    // Day change against last_equity, which is the previous session's close --
    // the same basis Alpaca uses, so this matches the broker rather than
    // inventing a baseline of our own.
    const chg = (equity != null && last) ? ((equity - last) / last) * 100 : null;
    const chgAbs = (equity != null && last != null) ? equity - last : null;

    const blocked = s && (s.trading_blocked === true || s.trading_blocked === 'true');
    const dot = !s ? 'dot-bad' : blocked ? 'dot-warn' : 'dot-ok';

    const top = el('div', 'tile-top');
    const name = el('div', 'tile-name');
    name.append(el('span', 'dot ' + dot), document.createTextNode(acct.label));
    top.append(name);

    const badge = el('span', 'badge ' + (chg == null ? 'badge-flat' : chg > 0 ? 'badge-up' : chg < 0 ? 'badge-down' : 'badge-flat'),
                     chg == null ? '—' : pct(chg));
    top.append(badge);
    tile.append(top);

    tile.append(el('div', 'tile-value', s ? money(equity) : '—'));
    tile.append(el('div', 'tile-meta',
      s ? (chgAbs == null ? 'no prior close' : signed(chgAbs) + ' today') : 'unreachable'));
    host.append(tile);
  }
}

/* ── equity chart (inline SVG; no chart library on a monitoring page) ────── */
function renderChart() {
  const wrap = $('#chart-wrap');
  const acct = state.chartAcct;
  const s = state.summaries[acct];
  const series = state.equity[acct] || [];
  $('#chart-sub').textContent = s ? '· ' + money(s.equity) : '';

  if (series.length < 2) {
    wrap.innerHTML = '';
    const msg = el('div', 'chart-empty',
      'Equity history builds up as the dashboard runs — one point per refresh. ' +
      'Nothing is stored server-side, so this resets when the page does.');
    wrap.append(msg);
    return;
  }

  const W = 900, H = 260, padL = 54, padR = 12, padT = 12, padB = 26;
  const ys = series.map(p => p.v);
  let lo = Math.min(...ys), hi = Math.max(...ys);
  if (hi === lo) { hi = lo + 1; lo = lo - 1; }
  const padY = (hi - lo) * 0.12;
  lo -= padY; hi += padY;

  const x = i => padL + (i / (series.length - 1)) * (W - padL - padR);
  const y = v => padT + (1 - (v - lo) / (hi - lo)) * (H - padT - padB);

  const rising = ys[ys.length - 1] >= ys[0];
  const stroke = rising ? '#2ebd85' : '#e5484d';

  let d = '';
  series.forEach((p, i) => { d += (i ? ' L' : 'M') + x(i).toFixed(1) + ' ' + y(p.v).toFixed(1); });
  const area = d + ` L${x(series.length - 1).toFixed(1)} ${H - padB} L${padL} ${H - padB} Z`;

  const gridVals = [lo + (hi - lo) * 0.1, (lo + hi) / 2, hi - (hi - lo) * 0.1];
  const grid = gridVals.map(v =>
    `<line x1="${padL}" y1="${y(v).toFixed(1)}" x2="${W - padR}" y2="${y(v).toFixed(1)}" stroke="#232d3d" stroke-width="1"/>` +
    `<text x="${padL - 8}" y="${(y(v) + 4).toFixed(1)}" text-anchor="end" fill="#5d6879" font-size="11">${Math.round(v).toLocaleString()}</text>`
  ).join('');

  const first = series[0], lastP = series[series.length - 1];
  const tLabel = t => new Date(t).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });

  wrap.innerHTML =
    `<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Equity over this session">
      <defs><linearGradient id="g" x1="0" x2="0" y1="0" y2="1">
        <stop offset="0%" stop-color="${stroke}" stop-opacity=".22"/>
        <stop offset="100%" stop-color="${stroke}" stop-opacity="0"/>
      </linearGradient></defs>
      ${grid}
      <path d="${area}" fill="url(#g)"/>
      <path d="${d}" fill="none" stroke="${stroke}" stroke-width="2" stroke-linejoin="round" stroke-linecap="round"/>
      <circle cx="${x(series.length - 1).toFixed(1)}" cy="${y(lastP.v).toFixed(1)}" r="3.5" fill="${stroke}"/>
      <text x="${padL}" y="${H - 8}" fill="#5d6879" font-size="11">${tLabel(first.t)}</text>
      <text x="${W - padR}" y="${H - 8}" text-anchor="end" fill="#5d6879" font-size="11">${tLabel(lastP.t)}</text>
    </svg>`;
}

/* ── right rail ─────────────────────────────────────────────────────────── */
function renderRail() {
  const host = $('#rail-positions');
  host.innerHTML = '';
  const rows = [];
  for (const acct of ACCOUNTS) {
    for (const p of (state.positions[acct.id] || [])) rows.push({ acct: acct.label, p });
  }
  if (!rows.length) {
    host.append(el('div', 'empty', 'No open positions. Stocks are flattened before the close.'));
  } else {
    rows.sort((a, b) => Math.abs(num(b.p.unrealized_pl) || 0) - Math.abs(num(a.p.unrealized_pl) || 0));
    for (const { acct, p } of rows) {
      const row = el('div', 'rail-row');
      const left = el('div');
      left.append(el('div', 'rail-sym', p.symbol));
      left.append(el('div', 'rail-sub', acct + ' · ' + (num(p.qty) ?? '—') + ' @ ' + money2(p.avg_entry_price)));
      const right = el('div', 'rail-val');
      const plpc = num(p.unrealized_plpc);
      right.append(el('div', 'rail-chg ' + cls(plpc), plpc == null ? '—' : pct(plpc * 100)));
      right.append(el('div', 'rail-sub ' + cls(p.unrealized_pl), signed(p.unrealized_pl)));
      row.append(left, right);
      host.append(row);
    }
  }

  const ah = $('#rail-alerts');
  ah.innerHTML = '';
  $('#alert-count').textContent = state.issues.length ? state.issues.length : '';
  if (!state.issues.length) {
    ah.append(el('div', 'empty', 'Nothing active. The watchdog sweeps every 15 minutes.'));
  } else {
    for (const it of state.issues.slice(0, 40)) ah.append(alertNode(it));
  }
}

function alertNode(it) {
  const node = el('div', 'alert');
  const sev = (it.severity || '').toLowerCase();
  node.append(el('div', 'alert-bar ' + (sev === 'advisory' ? 'alert-sev-low' : 'alert-sev-high')));
  const body = el('div', 'alert-body');
  body.append(el('div', 'alert-bot', [it.bot, it.key].filter(Boolean).join(' · ')));
  body.append(el('div', 'alert-msg', it.message || it.key || 'unnamed issue'));
  node.append(body);
  return node;
}

/* ── full views ─────────────────────────────────────────────────────────── */
function renderPositions() {
  const host = $('#positions-body');
  host.innerHTML = '';
  let any = false;
  for (const acct of ACCOUNTS) {
    const rows = state.positions[acct.id] || [];
    if (!rows.length) continue;
    any = true;
    const g = el('div', 'group');
    g.append(el('h3', 'group-title', acct.label));
    const panel = el('div', 'panel');
    const scroll = el('div', 'tbl-scroll');
    scroll.innerHTML =
      '<table class="tbl"><thead><tr><th>Symbol</th><th>Qty</th><th>Entry</th><th>Last</th>' +
      '<th>Value</th><th>P&amp;L</th><th>%</th></tr></thead><tbody>' +
      rows.map(p => `<tr>
        <td>${p.symbol}</td><td>${num(p.qty) ?? '—'}</td>
        <td>${money2(p.avg_entry_price)}</td><td>${money2(p.current_price)}</td>
        <td>${money(p.market_value)}</td>
        <td class="${cls(p.unrealized_pl)}">${signed(p.unrealized_pl)}</td>
        <td class="${cls(p.unrealized_plpc)}">${p.unrealized_plpc == null ? '—' : pct(num(p.unrealized_plpc) * 100)}</td>
      </tr>`).join('') + '</tbody></table>';
    panel.append(scroll); g.append(panel); host.append(g);
  }
  if (!any) host.append(el('div', 'panel')).append(el('div', 'empty', 'No open positions across the fleet.'));
}

function renderTrades() {
  const host = $('#trades-body');
  host.innerHTML = '';
  let any = false;
  for (const acct of ACCOUNTS) {
    const rows = (state.trades[acct.id] || []).slice(0, 15);
    if (!rows.length) continue;
    any = true;
    const g = el('div', 'group');
    g.append(el('h3', 'group-title', acct.label));
    const panel = el('div', 'panel');
    const scroll = el('div', 'tbl-scroll');
    scroll.innerHTML =
      '<table class="tbl"><thead><tr><th>Symbol</th><th>Closed</th><th>P&amp;L</th><th>%</th></tr></thead><tbody>' +
      rows.map(t => {
        const when = t.closed_at ? new Date(t.closed_at) : null;
        return `<tr>
          <td>${t.symbol || '—'}</td>
          <td>${when ? when.toLocaleString([], { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' }) : '—'}</td>
          <td class="${cls(t.pnl)}">${signed(t.pnl)}</td>
          <td class="${cls(t.pnl_pct)}">${t.pnl_pct == null ? '—' : pct(t.pnl_pct)}</td>
        </tr>`;
      }).join('') + '</tbody></table>';
    panel.append(scroll); g.append(panel); host.append(g);
  }
  if (!any) { const p = el('div', 'panel'); p.append(el('div', 'empty', 'No closed trades reported yet.')); host.append(p); }
}

function renderAlerts() {
  const host = $('#alerts-body');
  host.innerHTML = '';
  const panel = el('div', 'panel');
  const head = el('div', 'panel-head'); head.append(el('h2', null, 'Active alerts'));
  panel.append(head);
  if (!state.issues.length) panel.append(el('div', 'empty', 'Nothing active. The watchdog sweeps every 15 minutes.'));
  else for (const it of state.issues) panel.append(alertNode(it));
  host.append(panel);

  for (const acct of ACCOUNTS) {
    const r = state.readiness && state.readiness[acct.id];
    if (!r) continue;
    const p = el('div', 'panel'); p.style.marginTop = '14px';
    const h = el('div', 'panel-head'); h.append(el('h2', null, acct.label + ' readiness'));
    h.append(el('span', 'pill pill-muted', String(r.verdict || 'unknown')));
    p.append(h);
    const body = el('div', 'alert-body'); body.style.padding = '13px 15px';
    body.textContent = r.summary || r.detail || 'No detail reported.';
    p.append(body);
    host.append(p);
  }
}

/* ── data ───────────────────────────────────────────────────────────────── */
async function refresh() {
  try {
    const [issues, market] = await Promise.all([
      api('/api/issues').catch(() => []),
      api('/api/market-status').catch(() => null),
    ]);
    state.issues = Array.isArray(issues) ? issues : (issues && issues.issues) || [];

    const pill = $('#market-pill');
    if (market && typeof market.is_open === 'boolean') {
      pill.textContent = market.is_open ? 'Market open' : 'Market closed';
      pill.className = 'pill ' + (market.is_open ? 'pill-open' : 'pill-closed');
    } else { pill.textContent = 'market —'; pill.className = 'pill pill-muted'; }

    state.readiness = state.readiness || {};
    await Promise.all(ACCOUNTS.map(async a => {
      const [s, pos, tr, rd] = await Promise.all([
        api(`/api/accounts/${a.id}/summary`).catch(() => null),
        api(`/api/accounts/${a.id}/positions`).catch(() => []),
        api(`/api/accounts/${a.id}/trades`).catch(() => []),
        api(`/api/accounts/${a.id}/readiness`).catch(() => null),
      ]);
      state.summaries[a.id] = s;
      state.positions[a.id] = Array.isArray(pos) ? pos : (pos && pos.positions) || [];
      state.trades[a.id] = Array.isArray(tr) ? tr : (tr && tr.trades) || [];
      if (rd) state.readiness[a.id] = rd;
      // Equity series is built client-side from successive polls. Deliberately
      // not persisted: inventing a server-side history store for a monitoring
      // page would be a second source of truth about money.
      const eq = s ? num(s.equity) : null;
      if (eq != null) {
        (state.equity[a.id] = state.equity[a.id] || []).push({ t: Date.now(), v: eq });
        if (state.equity[a.id].length > 240) state.equity[a.id].shift();
      }
    }));

    $('#updated').textContent = 'updated ' + new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    renderTiles(); renderChart(); renderRail(); renderPositions(); renderTrades(); renderAlerts();
    showApp();
  } catch (e) {
    if (String(e.message) !== 'unauthenticated') console.error(e);
  }
}

/* ── wiring ─────────────────────────────────────────────────────────────── */
function setView(v) {
  $$('.view').forEach(n => n.classList.toggle('hidden', n.dataset.view !== v));
  $$('.nav-item').forEach(b => b.classList.toggle('is-active', b.dataset.view === v));
  window.scrollTo({ top: 0 });
}
$$('#nav .nav-item, #nav-mobile .nav-item').forEach(b => b.addEventListener('click', () => setView(b.dataset.view)));
$$('#chart-seg .seg-btn').forEach(b => b.addEventListener('click', () => {
  state.chartAcct = b.dataset.acct;
  $$('#chart-seg .seg-btn').forEach(x => x.classList.toggle('is-active', x === b));
  renderChart();
}));

$('#login-form').addEventListener('submit', async e => {
  e.preventDefault();
  $('#login-error').textContent = '';
  const res = await fetch('/api/login', {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    credentials: 'same-origin',
    body: JSON.stringify({ password: $('#password').value }),
  });
  if (res.ok) { $('#password').value = ''; showApp(); refresh(); }
  else if (res.status === 429) $('#login-error').textContent = 'Too many attempts, try again later';
  else $('#login-error').textContent = 'Wrong password';
});

$('#logout').addEventListener('click', async () => {
  await fetch('/api/logout', { method: 'POST', credentials: 'same-origin' }).catch(() => {});
  showLogin();
});

// Poll while visible only -- a backgrounded tab hitting Alpaca every 30s for
// hours is rate-limit pressure for data nobody is looking at.
let timer = null;
function startPolling() { stopPolling(); timer = setInterval(refresh, 30000); }
function stopPolling() { if (timer) { clearInterval(timer); timer = null; } }
document.addEventListener('visibilitychange', () => {
  if (document.hidden) stopPolling(); else { refresh(); startPolling(); }
});

refresh();
startPolling();
