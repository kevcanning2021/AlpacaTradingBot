const POLL_INTERVAL_MS = 15000;

let currentAccount = null;
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
  if (accounts.length) selectAccount(accounts[0].id, tabs.querySelector('.tab'));
}

function selectAccount(accountId, btnEl) {
  currentAccount = accountId;
  document.querySelectorAll('.tab').forEach((b) => b.classList.remove('active'));
  if (btnEl) btnEl.classList.add('active');
  refresh();
}

function money(v) {
  const n = parseFloat(v);
  return isNaN(n) ? v : n.toLocaleString('en-US', { style: 'currency', currency: 'USD' });
}

async function refresh() {
  if (!currentAccount) return;
  const banner = document.getElementById('offline-banner');
  try {
    const [summaryRes, positionsRes, ordersRes] = await Promise.all([
      api(`/api/accounts/${currentAccount}/summary`),
      api(`/api/accounts/${currentAccount}/positions`),
      api(`/api/accounts/${currentAccount}/orders`),
    ]);
    renderSummary(await summaryRes.json());
    renderPositions(await positionsRes.json());
    renderOrders(await ordersRes.json());
    banner.classList.add('hidden');
  } catch (e) {
    banner.classList.remove('hidden');
  }
}

function renderSummary(s) {
  document.getElementById('summary-card').innerHTML = `
    <h2>Account</h2>
    <div class="summary-grid">
      <div><span class="label">Equity</span><span class="value">${money(s.equity)}</span></div>
      <div><span class="label">Buying Power</span><span class="value">${money(s.buying_power)}</span></div>
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

function renderOrders(orders) {
  const tbody = document.querySelector('#orders-table tbody');
  tbody.innerHTML = '';
  orders.forEach((o) => {
    const row = document.createElement('tr');
    row.innerHTML = `<td>${o.symbol}</td><td>${o.side}</td><td>${o.status}</td>
      <td>${o.filled_avg_price ? money(o.filled_avg_price) : '-'}</td><td>${o.submitted_at || ''}</td>`;
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
