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

async function refresh() {
  const banner = document.getElementById('offline-banner');
  try {
    // agents-overview and issues are fetched together (not independently, like
    // before) because issues now render AS per-bot icons on the agent rows
    // themselves rather than as their own always-visible text panel -- see
    // renderAgentsOverview/renderInfraIssues.
    const [agents, decisions, issues] = await Promise.all([
      api('/api/agents-overview').then((r) => r.json()),
      api('/api/research-agent/decisions').then((r) => r.json()),
      api('/api/issues').then((r) => r.json()),
    ]);
    renderAgentsOverview(agents, issues);
    renderResearchAgentDecisions(decisions);
    renderInfraIssues(issues);

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
        <span class="${pnlClass}">${money(p.unrealized_pl)}</span>
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
