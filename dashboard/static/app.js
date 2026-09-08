const POLL_INTERVAL_MS = 15000;

let currentAccount = null;
let currentTab = 'account'; // 'account' or 'agents'
let pollTimer = null;
let showAllDecisions = false; // default: vetoes only -- most decisions are routine allows, not worth scanning past on mobile
let lastDecisions = [];

function showLogin(message) {
  document.getElementById('login-screen').classList.remove('hidden');
  document.getElementById('app-screen').classList.add('hidden');
  document.getElementById('login-error').textContent = message || '';
  if (pollTimer) clearInterval(pollTimer);
}

function showApp() {
  document.getElementById('login-screen').classList.add('hidden');
  document.getElementById('app-screen').classList.remove('hidden');
}

async function api(path, options) {
  const res = await fetch(path, Object.assign({ credentials: 'same-origin' }, options));
  if (res.status === 401) {
    showLogin();
    throw new Error('unauthorized');
  }
  return res;
}

async function loadAccounts() {
  const res = await api('/api/accounts');
  const accounts = await res.json();
  const tabs = document.getElementById('account-tabs');
  tabs.innerHTML = '';
  accounts.forEach((acct, i) => {
    const btn = document.createElement('button');
    btn.textContent = acct.label;
    btn.className = 'tab' + (i === 0 ? ' active' : '');
    btn.onclick = () => selectAccount(acct.id, btn);
    tabs.appendChild(btn);
  });
  const agentsBtn = document.createElement('button');
  agentsBtn.textContent = 'Agents';
  agentsBtn.className = 'tab';
  agentsBtn.onclick = () => selectAgentsTab(agentsBtn);
  tabs.appendChild(agentsBtn);

  if (accounts.length) selectAccount(accounts[0].id, tabs.querySelector('.tab'));
}

function selectAccount(accountId, btnEl) {
  currentAccount = accountId;
  currentTab = 'account';
  document.querySelectorAll('.tab').forEach((b) => b.classList.remove('active'));
  if (btnEl) btnEl.classList.add('active');
  document.getElementById('account-tab-content').classList.remove('hidden');
  document.getElementById('agents-tab-content').classList.add('hidden');
  refresh();
}

function selectAgentsTab(btnEl) {
  currentTab = 'agents';
  document.querySelectorAll('.tab').forEach((b) => b.classList.remove('active'));
  if (btnEl) btnEl.classList.add('active');
  document.getElementById('agents-tab-content').classList.remove('hidden');
  document.getElementById('account-tab-content').classList.add('hidden');
  refresh();
}

function money(v) {
  const n = parseFloat(v);
  return isNaN(n) ? v : n.toLocaleString('en-US', { style: 'currency', currency: 'USD' });
}

function pct(v) {
  const n = parseFloat(v);
  if (isNaN(n)) return '';
  const asPct = n * 100;
  return (asPct > 0 ? '+' : '') + asPct.toFixed(2) + '%';
}

async function refresh() {
  const banner = document.getElementById('offline-banner');
  try {
    // agents-overview and issues are fetched together (not independently, like
    // before) because issues now render AS per-bot icons on the agent rows
    // themselves rather than as their own always-visible text panel -- see
    // renderAgentsOverview/renderInfraIssues.
    const [agents, decisions, issues, fleetAudit, marketStatus] = await Promise.all([
      api('/api/agents-overview').then((r) => r.json()),
      api('/api/research-agent/decisions').then((r) => r.json()),
      api('/api/issues').then((r) => r.json()),
      api('/api/fleet-audit').then((r) => r.json()),
      api('/api/market-status').then((r) => r.json()),
    ]);
    renderAgentsOverview(agents, issues);
    renderResearchAgentDecisions(decisions);
    renderInfraIssues(issues);
    renderFleetAudit(fleetAudit);
    renderMarketStatus(marketStatus);

    const accountCalls = [];
    if (currentAccount) {
      accountCalls.push(
        api(`/api/accounts/${currentAccount}/summary`).then((r) => r.json()).then(renderSummary),
        api(`/api/accounts/${currentAccount}/positions`).then((r) => r.json()).then(renderPositions),
        api(`/api/accounts/${currentAccount}/orders`).then((r) => r.json()).then(renderOrders),
      );
    }
    await Promise.all(accountCalls);
    banner.classList.add('hidden');
  } catch (e) {
    banner.classList.remove('hidden');
  }
}

function healthBadge(health) {
  if (!health || health.healthy === null) return '<span class="badge badge-gray">Not tracked</span>';
  return health.healthy
    ? '<span class="badge badge-green">Working</span>'
    : '<span class="badge badge-red" title="' + (health.detail || '').replace(/"/g, '&quot;') + '">Problem</span>';
}

function escapeHtml(s) {
  return String(s).replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function issueFlag(issuesForThisBot) {
  return (issuesForThisBot && issuesForThisBot.length) ? '<span class="issue-flag">⚠️</span>' : '';
}

// Always-visible text, not hover/tap-to-reveal -- hover doesn't work on mobile
// (no hover state on a touchscreen), and tap-to-expand was rejected too:
// the actual message should just be there, scoped to the bot it's about,
// not hidden behind an interaction.
function issueText(issuesForThisBot) {
  if (!issuesForThisBot || !issuesForThisBot.length) return '';
  return issuesForThisBot.map((i) =>
    `<div class="agent-issue${i.severity === 'info' ? ' info' : ''}">${escapeHtml(i.message)}</div>`
  ).join('');
}

function renderAgentsOverview(agents, issues) {
  // Full error text used to live in one shared always-visible "Attention
  // Needed" panel -- replaced with a small warning flag plus the message
  // shown directly under the bot it's actually about, so it's still all
  // visible at a glance but organized per-bot instead of one combined dump.
  const byBot = {};
  for (const i of (issues || [])) {
    (byBot[i.bot] = byBot[i.bot] || []).push(i);
  }
  const el = document.getElementById('agents-overview');
  el.innerHTML = agents.map((a) => {
    const botIssues = byBot[a.label];
    return `
    <div class="agent-row">
      <div class="agent-row-top">
        <span class="agent-label-group">
          <span class="agent-label">${a.label}</span>
          ${issueFlag(botIssues)}
        </span>
        ${healthBadge(a.health)}
      </div>
      <div class="agent-role">${a.role}</div>
      ${a.health && a.health.detail && a.health.healthy !== false ? `<div class="agent-detail">${a.health.detail}</div>` : ''}
      ${issueText(botIssues)}
    </div>`;
  }).join('');
}

function renderInfraIssues(issues) {
  // Issues not tied to any specific bot (this dashboard's/watchdog's own repo
  // drift, an internal watchdog crash, etc.) -- same flag+text treatment,
  // just anchored next to the Agents heading instead of a bot row since
  // there's no bot card for them to sit on.
  const infra = (issues || []).filter((i) => i.bot === 'Infra');
  const flagEl = document.getElementById('infra-issue-icon');
  const textEl = document.getElementById('infra-issue-text');
  if (flagEl) flagEl.innerHTML = issueFlag(infra);
  if (textEl) textEl.innerHTML = issueText(infra);
}

function renderMarketStatus(status) {
  // Stock market closed (nights, weekends, holidays) is the normal state
  // for most of any given week -- not an error, so this is calm/blue, not
  // red like offline-banner. Exists so a quiet stock scan doesn't read as a
  // stuck bot: crypto keeps running 24/7 regardless, so everything else on
  // the dashboard still looks alive during a closure -- see the 2026-09-07
  // Labor Day case this was built for.
  const banner = document.getElementById('market-status-banner');
  if (!status || status.is_open) {
    banner.classList.add('hidden');
    return;
  }
  const reopens = status.next_open
    ? new Date(status.next_open).toLocaleString([], { weekday: 'short', month: 'short', day: 'numeric', hour: 'numeric', minute: '2-digit' })
    : 'soon';
  banner.textContent = `Stock market closed — reopens ${reopens}. Crypto keeps trading 24/7 regardless.`;
  banner.classList.remove('hidden');
}

function renderResearchAgentDecisions(decisions) {
  lastDecisions = decisions;
  renderFilteredDecisions();
}

function renderFilteredDecisions() {
  const list = document.getElementById('research-agent-list');
  const shown = showAllDecisions ? lastDecisions : lastDecisions.filter((d) => d.veto);
  list.innerHTML = '';
  shown.forEach((d) => {
    const item = document.createElement('div');
    item.className = 'decision-item';
    const vetoBadge = d.veto ? '<span class="badge badge-red">Blocked</span>' : '<span class="badge badge-green">Allowed</span>';
    const conf = (d.confidence === null || d.confidence === undefined) ? '' : `<span>${Math.round(d.confidence * 100)}%</span>`;
    const flags = (d.risk_flags || []).map((f) => `<span class="tag">${f}</span>`).join('');
    const bot = d.bot ? d.bot[0].toUpperCase() + d.bot.slice(1) : '-';
    item.innerHTML = `
      <div class="item-top">
        <span class="item-title">${d.symbol}</span>
        <span class="item-sub">${bot}</span>
        ${vetoBadge}
      </div>
      <div class="item-meta">
        <span>${niceTime(d.timestamp)}</span>
        ${conf}
        ${flags}
      </div>
      <div class="item-detail hidden">${d.reasoning || ''}</div>`;
    item.addEventListener('click', () => item.querySelector('.item-detail').classList.toggle('hidden'));
    list.appendChild(item);
  });
  if (!shown.length) {
    list.innerHTML = `<p class="hint">${showAllDecisions ? 'No decisions logged yet' : 'No vetoes yet — nothing blocked so far'}</p>`;
  }
}

function renderFleetAudit(entries) {
  // Main/Sofi's backtest is %-based (scanner.py's engine), Nova's is R-based
  // (bot/backtester.py's engine, since its risk sizing is R-multiple-native)
  // -- two different shapes from two genuinely different engines, not an
  // inconsistency to paper over. Render whichever fields a given entry
  // actually has rather than forcing one shape.
  const list = document.getElementById('fleet-audit-list');
  const order = { main: 0, sofi: 1, nova: 2 };
  const sorted = [...entries].sort((a, b) => (order[a.bot] ?? 9) - (order[b.bot] ?? 9));
  list.innerHTML = '';
  sorted.forEach((e) => {
    const bot = e.bot ? e.bot[0].toUpperCase() + e.bot.slice(1) : '-';
    const bt = e.backtest || {};
    const trades = bt.trade_count !== undefined ? bt.trade_count : bt.total_trades;
    const win = bt.win_rate_pct !== undefined ? bt.win_rate_pct : bt.long_win_rate_pct;
    const expectancy = bt.expectancy_pct !== undefined
      ? `${bt.expectancy_pct > 0 ? '+' : ''}${bt.expectancy_pct}%/trade`
      : (bt.long_avg_r !== undefined ? `${bt.long_avg_r > 0 ? '+' : ''}${bt.long_avg_r}R avg` : '');
    const ft = e.forward_test || {};
    const ftResult = ft.total_pnl !== undefined ? money(ft.total_pnl)
      : (ft.total_r !== undefined ? `${ft.total_r > 0 ? '+' : ''}${ft.total_r}R` : '');
    const driftBadge = e.drift_detected ? '<span class="badge badge-red" title="Watchlist or threshold changed since yesterday\'s run">Drift</span>' : '';
    const stops = e.stop_distances || [];
    const stopTags = stops.length
      ? stops.map((s) => `<span class="tag${s.near_stop ? ' tag-warn' : ''}">${s.symbol} ${s.pct_of_the_way_to_stop}% to stop</span>`).join('')
      : '<span class="hint" style="margin:0;">No open positions</span>';

    const item = document.createElement('div');
    item.className = 'decision-item';
    item.innerHTML = `
      <div class="item-top">
        <span class="item-title">${bot}</span>
        <span class="item-sub">${e.date || ''}</span>
        ${driftBadge}
      </div>
      <div class="item-meta">
        <span>${trades !== undefined ? trades + ' backtest trades' : 'no backtest yet'}</span>
        ${win !== undefined ? `<span>${win}% win</span>` : ''}
        ${expectancy ? `<span>${expectancy}</span>` : ''}
      </div>
      <div class="item-meta">
        <span>Forward test: ${ft.closed_trades || 0} real trade${ft.closed_trades === 1 ? '' : 's'}${ftResult ? ', ' + ftResult : ''}</span>
      </div>
      <div class="item-meta">${stopTags}</div>`;
    list.appendChild(item);
  });
  if (!sorted.length) {
    list.innerHTML = '<p class="hint">No audit data yet — each bot\'s first daily run hasn\'t fired</p>';
  }
}

function renderSummary(s) {
  document.getElementById('summary-card').innerHTML = `
    <h2>Account</h2>
    <div class="summary-grid">
      <div><span class="label">Total Value</span><span class="value">${money(s.equity)}</span></div>
      <div><span class="label">Cash</span><span class="value">${money(s.cash)}</span></div>
      <div><span class="label">Status</span><span class="value">${s.status || ''}</span></div>
    </div>`;
}

function renderProtectionLine(p) {
  // Main/Sofi track a peak price and ratchet a trailing stop off it
  // (trader.py: _handle_trailing_stop) alongside a fixed entry-anchored
  // stop -- whichever is currently higher (tighter) is the one that
  // actually protects the position; a trailing stop above the entry stop
  // is what "moved up" looks like. Nova has no trailing mechanism at all,
  // so its stop/target are shown as fixed, with no peak.
  if (p.peak_price !== undefined) {
    const trailing = p.trailing_stop_price;
    const entryStop = p.entry_stop_price;
    const trailingIsActive = trailing !== null && entryStop !== null && trailing > entryStop;
    return `
      <div class="item-meta">
        <span>Peak ${money(p.peak_price)}</span>
        <span>Entry stop ${money(entryStop)}</span>
        <span class="${trailingIsActive ? 'trailing-active' : ''}">
          ${trailingIsActive ? '▲ ' : ''}Trailing stop ${money(trailing)}
        </span>
      </div>`;
  }
  if (p.stop_price !== undefined) {
    return `
      <div class="item-meta">
        <span>Stop ${money(p.stop_price)}</span>
        <span>Target ${money(p.target_price)}</span>
        <span class="item-sub">(fixed at entry, no trailing)</span>
      </div>`;
  }
  return '';
}

function renderPositions(positions) {
  const list = document.getElementById('positions-list');
  list.innerHTML = '';
  positions.forEach((p) => {
    const pnl = parseFloat(p.unrealized_pl);
    const pnlClass = pnl > 0 ? 'positive' : pnl < 0 ? 'negative' : '';
    const item = document.createElement('div');
    item.className = 'decision-item';
    item.innerHTML = `
      <div class="item-top">
        <span class="item-title">${p.symbol}</span>
        <span class="item-sub">${p.qty} sh</span>
        <span class="${pnlClass}">${money(p.unrealized_pl)} <span class="tag">${pct(p.unrealized_plpc)}</span></span>
      </div>
      <div class="item-meta">
        <span>Bought ${money(p.avg_entry_price)}</span>
        <span>Now ${money(p.current_price)}</span>
      </div>
      ${renderProtectionLine(p)}`;
    list.appendChild(item);
  });
  if (!positions.length) list.innerHTML = '<p class="hint">No open positions</p>';
}

function niceTime(iso) {
  return (iso || '').replace('T', ' ').slice(0, 16);
}

function renderOrders(orders) {
  const list = document.getElementById('orders-list');
  list.innerHTML = '';
  orders.forEach((o) => {
    const item = document.createElement('div');
    item.className = 'decision-item';
    item.innerHTML = `
      <div class="item-top">
        <span class="item-title">${o.symbol}</span>
        <span class="item-sub">${o.side}</span>
        <span class="tag">${o.status}</span>
      </div>
      <div class="item-meta">
        <span>${o.filled_avg_price ? money(o.filled_avg_price) : 'not filled'}</span>
        <span>${niceTime(o.submitted_at)}</span>
      </div>`;
    list.appendChild(item);
  });
  if (!orders.length) list.innerHTML = '<p class="hint">No orders yet</p>';
}

document.getElementById('login-form').addEventListener('submit', async (e) => {
  e.preventDefault();
  const password = document.getElementById('password').value;
  const res = await fetch('/api/login', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ password }),
  });
  if (res.ok) {
    document.getElementById('password').value = '';
    showApp();
    await loadAccounts();
    pollTimer = setInterval(refresh, POLL_INTERVAL_MS);
  } else if (res.status === 429) {
    document.getElementById('login-error').textContent = 'Too many attempts, try again later';
  } else {
    document.getElementById('login-error').textContent = 'Wrong password';
  }
});

document.getElementById('logout-btn').addEventListener('click', async () => {
  await fetch('/api/logout', { method: 'POST' });
  showLogin();
});

document.getElementById('research-agent-toggle').addEventListener('click', (e) => {
  const body = document.getElementById('research-agent-body');
  const nowHidden = body.classList.toggle('hidden');
  e.target.textContent = 'Research Agent Decisions ' + (nowHidden ? '▸' : '▾');
});

document.getElementById('research-agent-filter-toggle').addEventListener('click', (e) => {
  showAllDecisions = !showAllDecisions;
  e.target.textContent = showAllDecisions ? 'Vetoes only' : 'Show all';
  renderFilteredDecisions();
});

document.getElementById('fleet-audit-toggle').addEventListener('click', (e) => {
  const body = document.getElementById('fleet-audit-body');
  const nowHidden = body.classList.toggle('hidden');
  e.target.textContent = 'Daily Fleet Audit ' + (nowHidden ? '▸' : '▾');
});

(async function init() {
  try {
    await loadAccounts();
    showApp();
    pollTimer = setInterval(refresh, POLL_INTERVAL_MS);
  } catch (e) {
    showLogin();
  }
})();

if ('serviceWorker' in navigator) {
  navigator.serviceWorker.register('/static/sw.js');
}
