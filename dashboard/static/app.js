const POLL_INTERVAL_MS = 15000;

let currentAccount = null;
let currentTab = 'account'; // 'account' or 'agents'
let pollTimer = null;

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
    const calls = [
      api('/api/agents-overview').then((r) => r.json()).then(renderAgentsOverview),
      api('/api/research-agent/decisions').then((r) => r.json()).then(renderResearchAgentDecisions),
      api('/api/issues').then((r) => r.json()).then(renderIssues),
    ];
    if (currentAccount) {
      calls.push(
        api(`/api/accounts/${currentAccount}/summary`).then((r) => r.json()).then(renderSummary),
        api(`/api/accounts/${currentAccount}/positions`).then((r) => r.json()).then(renderPositions),
        api(`/api/accounts/${currentAccount}/orders`).then((r) => r.json()).then(renderOrders),
      );
    }
    await Promise.all(calls);
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

function renderAgentsOverview(agents) {
  const el = document.getElementById('agents-overview');
  el.innerHTML = agents.map((a) => `
    <div class="agent-row">
      <div class="agent-row-top">
        <span class="agent-label">${a.label}</span>
        ${healthBadge(a.health)}
      </div>
      <div class="agent-role">${a.role}</div>
      ${a.health && a.health.detail && a.health.healthy !== false ? `<div class="agent-detail">${a.health.detail}</div>` : ''}
    </div>`).join('');
}

function renderResearchAgentDecisions(decisions) {
  const tbody = document.querySelector('#research-agent-table tbody');
  tbody.innerHTML = '';
  decisions.forEach((d) => {
    const row = document.createElement('tr');
    row.className = 'expandable';
    const vetoBadge = d.veto ? '<span class="badge badge-red">Blocked</span>' : '<span class="badge badge-green">Allowed</span>';
    const conf = (d.confidence === null || d.confidence === undefined) ? '-' : Math.round(d.confidence * 100) + '%';
    const flags = (d.risk_flags || []).map((f) => `<span class="tag">${f}</span>`).join('');
    const bot = d.bot ? d.bot[0].toUpperCase() + d.bot.slice(1) : '-';
    row.innerHTML = `<td>${niceTime(d.timestamp)}</td><td>${bot}</td><td>${d.symbol}</td>
      <td>${vetoBadge}</td><td>${conf}</td><td>${flags}</td>`;
    const detail = document.createElement('tr');
    detail.className = 'reasoning-row hidden';
    detail.innerHTML = `<td colspan="6">${d.reasoning || ''}</td>`;
    row.addEventListener('click', () => detail.classList.toggle('hidden'));
    tbody.appendChild(row);
    tbody.appendChild(detail);
  });
  if (!decisions.length) tbody.innerHTML = '<tr><td colspan="6">No decisions logged yet</td></tr>';
}

const ISSUE_SOURCE_LABELS = {
  'watchdog': 'watchdog',
  'strategy_check': 'strategy check',
  'fleet-review': 'fleet review',  // retired 2026-08-31; kept in case old state ever lingers
};

// Fixed display order so groups don't jump around between polls -- matches
// the account ordering used everywhere else on the dashboard (Main/Sofi/Nova),
// with Infra (dashboard/watchdog's own repo, retired components) last since
// it's not a trading bot.
const BOT_GROUP_ORDER = ['Main', 'Sofi', 'Nova', 'Infra'];

function renderIssues(issues) {
  const card = document.getElementById('issues-card');
  const body = document.getElementById('issues-body');
  card.classList.remove('issues-alert', 'issues-clear', 'issues-info');
  if (!issues.length) {
    card.classList.add('issues-clear');
    body.innerHTML = 'All clear — no active issues.';
    return;
  }
  // 'info' issues are known and already handled (e.g. crypto trading paused
  // because its backtest went negative) -- still worth showing so it's clear
  // why, but shouldn't look like something needs attention. Only escalate the
  // whole card to the red alert state if at least one issue actually needs a
  // look; an all-info set gets the calmer 'tracking' treatment instead.
  const needsAttention = issues.some((i) => i.severity !== 'info');
  card.classList.add(needsAttention ? 'issues-alert' : 'issues-info');

  // Grouped per-bot (not one flat list) so it's immediately clear which bot
  // each issue belongs to, rather than having to read every message to tell.
  const byBot = new Map();
  for (const i of issues) {
    if (!byBot.has(i.bot)) byBot.set(i.bot, []);
    byBot.get(i.bot).push(i);
  }
  const orderedBots = [...BOT_GROUP_ORDER.filter((b) => byBot.has(b)),
                        ...[...byBot.keys()].filter((b) => !BOT_GROUP_ORDER.includes(b))];

  body.innerHTML = orderedBots.map((bot) => `
    <div class="issue-group">
      <div class="issue-group-label">${bot}</div>
      ${byBot.get(bot).map((i) => `
        <div class="issue-row${i.severity === 'info' ? ' info' : ''}">
          <span class="tag">${ISSUE_SOURCE_LABELS[i.source] || i.source}</span>
          <div class="issue-message">${i.message}</div>
          <div class="issue-since">Since ${niceTime(i.first_seen)}</div>
        </div>`).join('')}
    </div>`).join('');
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

function renderPositions(positions) {
  const tbody = document.querySelector('#positions-table tbody');
  tbody.innerHTML = '';
  positions.forEach((p) => {
    const pnl = parseFloat(p.unrealized_pl);
    const pnlClass = pnl > 0 ? 'positive' : pnl < 0 ? 'negative' : '';
    const row = document.createElement('tr');
    row.innerHTML = `<td>${p.symbol}</td><td>${p.qty}</td><td>${money(p.avg_entry_price)}</td>
      <td>${money(p.current_price)}</td><td class="${pnlClass}">${money(p.unrealized_pl)}</td>`;
    tbody.appendChild(row);
  });
  if (!positions.length) tbody.innerHTML = '<tr><td colspan="5">No open positions</td></tr>';
}

function niceTime(iso) {
  return (iso || '').replace('T', ' ').slice(0, 16);
}

function renderOrders(orders) {
  const tbody = document.querySelector('#orders-table tbody');
  tbody.innerHTML = '';
  orders.forEach((o) => {
    const row = document.createElement('tr');
    row.innerHTML = `<td>${o.symbol}</td><td>${o.side}</td><td>${o.status}</td>
      <td>${o.filled_avg_price ? money(o.filled_avg_price) : '-'}</td><td>${niceTime(o.submitted_at)}</td>`;
    tbody.appendChild(row);
  });
  if (!orders.length) tbody.innerHTML = '<tr><td colspan="5">No orders yet</td></tr>';
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
