/* ================================================================
   AI Finance Controller — Main Application Logic
   ================================================================ */

const API = '';  // Same origin
let authToken = localStorage.getItem('afc_token');
let currentPage = 'dashboard';
let isAuthRegister = false;
let currentResults = null;
let currentMetrics = null;
let currentFilter = 'all';

// ================================================================ INIT
document.addEventListener('DOMContentLoaded', () => {
  if (authToken) {
    loadDashboard();
  } else {
    showPage('auth');
  }
  setupDragDrop();
});

// ================================================================ NAVIGATION
function navigate(page) {
  if (!authToken && page !== 'auth') {
    showPage('auth');
    return;
  }
  showPage(page);
  if (page === 'dashboard') loadDashboard();
  if (page === 'settings') loadSettings();
}

function showPage(page) {
  currentPage = page;
  document.querySelectorAll('.page').forEach(p => {
    p.style.display = 'none';
    p.classList.remove('active');
  });
  const el = document.getElementById(`page-${page}`);
  if (el) {
    el.style.display = 'block';
    el.classList.add('active');
  }
  updateNavbar();
}

function updateNavbar() {
  const actions = document.getElementById('nav-actions');
  if (!authToken) {
    actions.innerHTML = '';
    return;
  }
  const email = localStorage.getItem('afc_email') || '';
  const initial = email.charAt(0).toUpperCase();
  actions.innerHTML = `
    <button class="nav-btn ${currentPage === 'dashboard' ? 'active' : ''}" onclick="navigate('dashboard')">📊 Dashboard</button>
    <button class="nav-btn ${currentPage === 'settings' ? 'active' : ''}" onclick="navigate('settings')">⚙️ Settings</button>
    <div class="nav-user">
      <div class="nav-avatar">${initial}</div>
      <span style="max-width:120px;overflow:hidden;text-overflow:ellipsis">${email}</span>
    </div>
    <button class="nav-btn" onclick="logout()" style="color:var(--unresolved)">↪ Logout</button>
  `;
}

// ================================================================ AUTH
function toggleAuthMode() {
  isAuthRegister = !isAuthRegister;
  document.getElementById('auth-title').textContent = isAuthRegister ? 'Create Account' : 'Sign In';
  document.getElementById('auth-subtitle').textContent = isAuthRegister
    ? 'Start managing your reconciliation' : 'Welcome back to your finance dashboard';
  document.getElementById('auth-submit').textContent = isAuthRegister ? 'Create Account' : 'Sign In';
  document.getElementById('auth-footer').innerHTML = isAuthRegister
    ? 'Already have an account? <a href="#" onclick="toggleAuthMode()">Sign in</a>'
    : 'Don\'t have an account? <a href="#" onclick="toggleAuthMode()">Create one</a>';
}

async function handleAuth(e) {
  e.preventDefault();
  const email = document.getElementById('auth-email').value.trim();
  const password = document.getElementById('auth-password').value;
  const endpoint = isAuthRegister ? '/api/auth/register' : '/api/auth/login';

  try {
    const res = await apiFetch(endpoint, { method: 'POST', body: { email, password } });
    authToken = res.token;
    localStorage.setItem('afc_token', res.token);
    localStorage.setItem('afc_email', res.user.email);
    showToast('success', `Welcome${isAuthRegister ? '' : ' back'}, ${res.user.email}!`);
    navigate('dashboard');
  } catch (err) {
    showToast('error', err.message || 'Authentication failed');
  }
}

function logout() {
  authToken = null;
  localStorage.removeItem('afc_token');
  localStorage.removeItem('afc_email');
  currentResults = null;
  currentMetrics = null;
  showPage('auth');
  showToast('info', 'Logged out successfully');
}

// ================================================================ DASHBOARD
async function loadDashboard() {
  showPage('dashboard');
  try {
    const data = await apiFetch('/api/results');
    currentResults = data.results;
    currentMetrics = data.metrics;
    renderDashboard(data);
  } catch (err) {
    // No data yet — show empty state
    document.getElementById('dashboard-empty').style.display = 'block';
    document.getElementById('dashboard-content').style.display = 'none';
  }
}

function renderDashboard(data) {
  document.getElementById('dashboard-empty').style.display = 'none';
  document.getElementById('dashboard-content').style.display = 'block';

  const results = data.results;
  const metrics = data.metrics?.metrics || data.metrics || {};
  const decisions = results.decisions || [];

  // Store flows for click handler
  window._currentFlows = results.payment_flows || [];

  // ---- KPI Cards ----
  const stateCounts = {};
  for (const d of decisions) {
    const s = d.state || 'UNKNOWN';
    stateCounts[s] = (stateCounts[s] || 0) + 1;
  }

  animateNumber('kpi-total', decisions.length);
  animateNumber('kpi-verified', stateCounts.VERIFIED || 0);
  animateNumber('kpi-explained', stateCounts.EXPLAINED || 0);
  animateNumber('kpi-human', (stateCounts.HUMAN_REVIEW || 0) + (stateCounts.AWAITING_BANK || 0));
  animateNumber('kpi-unresolved', stateCounts.UNRESOLVED || 0);

  document.getElementById('kpi-total-sub').textContent =
    `${metrics.split || 'live'} · seed ${metrics.seed || 0}`;

  // ---- Charts ----
  destroyCharts();
  renderStateDonut(stateCounts);
  renderConfidenceChart(decisions);
  renderMoneyChart(metrics);

  // ---- Gauges ----
  const coverage = metrics.coverage_terminal_without_human || 0;
  const accuracy = metrics.confusion?.accuracy ?? (decisions.length > 0 ? 1 : 0);

  // Calculate match rate from match summary
  const matchSummary = results.match_summary || {};
  const totalMatches = Object.values(matchSummary).reduce((a, b) => a + b, 0);
  const keyedMatches = (matchSummary.keyed || 0) + (matchSummary.inferred || 0);
  const matchRate = totalMatches > 0 ? keyedMatches / totalMatches : 0;

  renderGauge('gauge-coverage', coverage, 1, CHART_COLORS.explained);
  renderGauge('gauge-accuracy', accuracy, 1, CHART_COLORS.verified);
  renderGauge('gauge-match-rate', matchRate, 1, CHART_COLORS.accent);

  const throughput = data.metrics?.run_meta?.records_per_second || results.wall_clock_seconds
    ? Math.round(decisions.length / (results.wall_clock_seconds || 1))
    : 0;
  document.getElementById('gauge-throughput').textContent =
    throughput.toLocaleString('en-IN');

  // ---- Exception Table ----
  renderExceptionTable(decisions);

  // ---- Run Info ----
  const runMeta = data.metrics?.run_meta || {};
  document.getElementById('run-info').textContent =
    `Run: ${data.run_at || 'just now'} · ${runMeta.wall_clock_seconds || results.wall_clock_seconds || '?'}s · ` +
    `${runMeta.mode || metrics.split || 'unknown'} mode`;
}

function renderExceptionTable(decisions) {
  const tbody = document.getElementById('exception-table-body');
  if (!tbody) return;

  let filtered = decisions;
  if (currentFilter !== 'all') {
    filtered = decisions.filter(d => d.state === currentFilter);
  }

  // Sort: unresolved first, then human review, then rest
  const stateOrder = { UNRESOLVED: 0, HUMAN_REVIEW: 1, AWAITING_BANK: 2, EXPLAINED: 3, VERIFIED: 4 };
  filtered.sort((a, b) => (stateOrder[a.state] ?? 5) - (stateOrder[b.state] ?? 5));

  document.getElementById('exception-count').textContent = `${filtered.length} transactions`;

  // Update filter chips with counts
  const allChips = document.querySelectorAll('.filter-chip');
  allChips.forEach(chip => {
    chip.classList.remove('active');
    const filterVal = chip.getAttribute('onclick')?.match(/'(\w+)'/)?.[1];
    if (filterVal === currentFilter) chip.classList.add('active');
  });

  if (filtered.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="7" style="text-align:center;padding:40px;color:var(--text-muted)">
          ${currentFilter === 'all' ? '✅ No transactions found' : `No ${currentFilter.replace('_', ' ').toLowerCase()} transactions`}
        </td>
      </tr>`;
    return;
  }

  tbody.innerHTML = filtered.slice(0, 200).map(d => {
    const badgeClass = {
      VERIFIED: 'badge-verified',
      EXPLAINED: 'badge-explained',
      AWAITING_BANK: 'badge-awaiting',
      HUMAN_REVIEW: 'badge-human-review',
      UNRESOLVED: 'badge-unresolved',
    }[d.state] || '';

    const conf = d.confidence?.score ?? 0;
    const confColor = conf >= 0.9 ? 'var(--verified)' :
                      conf >= 0.6 ? 'var(--awaiting)' : 'var(--unresolved)';

    const routingLabel = {
      auto_reconcile: '🟢 Auto',
      ai_suggestion_human_confirms: '🟡 AI+Human',
      human_review_queue: '🔴 Human',
    }[d.routing] || d.routing;

    return `
      <tr onclick="showTransactionDetail('${d.unit_id}')">
        <td class="mono" style="font-size:12px;color:var(--text-accent)">${d.unit_id}</td>
        <td>${d.unit_kind || 'payment'}</td>
        <td><span class="badge ${badgeClass}">${d.state}</span></td>
        <td class="mono" style="font-size:12px">${d.rule}</td>
        <td>
          <span class="mono" style="color:${confColor};font-size:12px;font-weight:600">${conf.toFixed(3)}</span>
          <span style="font-size:10px;color:var(--text-muted);margin-left:4px">(${d.confidence?.passed || 0}/${d.confidence?.applicable || 0})</span>
        </td>
        <td style="font-size:12px">${routingLabel}</td>
        <td><button class="btn btn-sm btn-secondary" onclick="event.stopPropagation();showTransactionDetail('${d.unit_id}')">View</button></td>
      </tr>`;
  }).join('');
}

function filterTable(state) {
  currentFilter = state;
  if (currentResults) {
    renderExceptionTable(currentResults.decisions || []);
  }
}

// ================================================================ TRANSACTION DETAIL
function showTransactionDetail(unitId) {
  // Show flow
  renderFlowForTransaction(unitId);

  // Show modal
  const decisions = currentResults?.decisions || [];
  const decision = decisions.find(d => d.unit_id === unitId);
  if (!decision) return;

  const flows = window._currentFlows || [];
  const flow = flows.find(f => f.unit_id === unitId);

  const decompositions = currentResults?.decompositions || [];
  let decomp = null;
  if (flow?.settlement?.settlement_id) {
    decomp = decompositions.find(d => d.settlement_id === flow.settlement.settlement_id);
  }

  const paise = (p) => {
    if (p == null) return '—';
    const sign = p < 0 ? '−' : '';
    const abs = Math.abs(p / 100);
    return `${sign}₹${abs.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  };

  let decompHtml = '';
  if (decomp) {
    decompHtml = `
      <div style="margin-top:16px">
        <h4 style="font-size:14px;margin-bottom:8px">Gap Analysis</h4>
        <div style="display:grid;grid-template-columns:1fr 1fr;gap:8px;font-size:13px">
          <div>Expected (contracted): <strong class="mono">${paise(decomp.expected_paise)}</strong></div>
          <div>Actual bank credit: <strong class="mono">${paise(decomp.actual_paise)}</strong></div>
          <div>Gap: <strong class="mono" style="color:${decomp.gap_paise !== 0 ? 'var(--unresolved)' : 'var(--verified)'}">${paise(decomp.gap_paise)}</strong></div>
          <div>Unexplained: <strong class="mono" style="color:${decomp.unexplained_paise !== 0 ? 'var(--human-review)' : 'var(--verified)'}">${paise(decomp.unexplained_paise)}</strong></div>
        </div>
        ${decomp.attributed?.length ? `
          <div style="margin-top:12px">
            <div style="font-size:12px;color:var(--text-muted);margin-bottom:6px">Attributed Causes:</div>
            ${decomp.attributed.map(a => `
              <div style="padding:8px 12px;background:var(--bg-input);border-radius:6px;margin-bottom:4px;font-size:12px">
                <div style="display:flex;justify-content:space-between">
                  <span style="color:var(--text-accent)">${a.cause}</span>
                  <span class="mono" style="font-weight:600">${paise(a.amount_paise)}</span>
                </div>
                <div style="color:var(--text-muted);font-size:11px;margin-top:2px">${a.proof || ''}</div>
              </div>
            `).join('')}
          </div>
        ` : ''}
      </div>`;
  }

  const checksHtml = decision.confidence?.checks
    ? Object.entries(decision.confidence.checks).map(([name, val]) => {
      const icon = val === true ? '✅' : val === false ? '❌' : '⚪';
      return `<span style="font-size:11px;margin-right:8px" title="${name}">${icon} ${name.replace(/_/g, ' ')}</span>`;
    }).join('')
    : '';

  const modal = document.getElementById('txn-modal');
  document.getElementById('txn-modal-title').textContent = `Transaction: ${unitId}`;
  document.getElementById('txn-modal-body').innerHTML = `
    <div style="display:flex;align-items:center;gap:12px;margin-bottom:16px">
      <span class="badge badge-${decision.state.toLowerCase().replace('_','-')}" style="font-size:13px;padding:6px 16px">
        ${decision.state}
      </span>
      <span style="font-size:13px;color:var(--text-muted)">Rule ${decision.rule} · Confidence ${(decision.confidence?.score || 0).toFixed(3)}</span>
    </div>

    ${flow?.payment ? `
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px;font-size:13px;margin-bottom:16px">
      <div class="card" style="padding:12px">
        <div class="card-title" style="margin-bottom:4px">Payment</div>
        <div class="mono" style="font-size:12px">${flow.payment.payment_id}</div>
        <div style="font-size:18px;font-weight:700;margin-top:4px" class="num">${paise(flow.payment.gross_paise)}</div>
        <div style="font-size:11px;color:var(--text-muted)">${flow.payment.captured_on}</div>
      </div>
      <div class="card" style="padding:12px">
        <div class="card-title" style="margin-bottom:4px">Settlement</div>
        <div class="mono" style="font-size:12px">${flow.settlement?.settlement_id || 'N/A'}</div>
        <div style="font-size:11px;color:var(--text-muted);margin-top:4px">UTR: ${flow.settlement?.utr || 'None'}</div>
        <div style="font-size:11px;color:var(--text-muted)">Due: ${flow.settlement?.due_on || 'N/A'}</div>
      </div>
    </div>` : ''}

    ${(() => {
      if (!flow?.payment) return '';
      const gross = flow.payment.gross_paise;
      const mdr_bps = 190;
      const gst_bps = 1800;

      const calcFee = (amt, bps) => {
        const num = amt * bps;
        const den = 10000;
        if (num >= 0) return Math.floor((num * 2 + den) / (den * 2));
        return -Math.floor((-num * 2 + den) / (den * 2));
      };

      const mdr_paise = calcFee(gross, mdr_bps);
      const gst_paise = calcFee(mdr_paise, gst_bps);
      const net_paise = gross - mdr_paise - gst_paise;

      return `
      <div style="margin-top:16px">
        <h4 style="font-size:14px;margin-bottom:8px">Tax & Fee Segregation (Contracted Rates)</h4>
        <div class="card" style="padding:12px;display:flex;flex-direction:column;gap:8px;font-size:13px;background:var(--bg-input)">
          <div style="display:flex;justify-content:space-between">
            <span style="color:var(--text-muted)">Gross Payment:</span>
            <span class="mono" style="font-weight:600">${paise(gross)}</span>
          </div>
          <div style="display:flex;justify-content:space-between">
            <span style="color:var(--text-muted)">MDR Fee (1.90%):</span>
            <span class="mono" style="color:var(--unresolved)">${paise(-mdr_paise)}</span>
          </div>
          <div style="display:flex;justify-content:space-between">
            <span style="color:var(--text-muted)">GST on Fee (18.00%):</span>
            <span class="mono" style="color:var(--unresolved)">${paise(-gst_paise)}</span>
          </div>
          <div style="height:1px;background:var(--border-subtle);margin:4px 0"></div>
          <div style="display:flex;justify-content:space-between">
            <span style="font-weight:600">Expected Net Bank Credit:</span>
            <span class="mono" style="font-weight:700;color:var(--verified)">${paise(net_paise)}</span>
          </div>
        </div>
      </div>`;
    })()}

    ${decompHtml}

    <div style="margin-top:16px">
      <div style="font-size:12px;color:var(--text-muted);margin-bottom:6px">Confidence Checks:</div>
      <div style="line-height:2">${checksHtml}</div>
    </div>
  `;

  modal.classList.add('visible');
}

function closeModal() {
  document.getElementById('txn-modal').classList.remove('visible');
}

// ================================================================ SETTINGS
async function loadSettings() {
  try {
    const status = await apiFetch('/api/razorpay/status');
    const statusEl = document.getElementById('razorpay-status');
    if (status.connected) {
      statusEl.className = 'connection-status connected';
      statusEl.textContent = `✓ Connected — ${status.key_id} (Test Mode)`;
    } else {
      statusEl.className = 'connection-status disconnected';
      statusEl.textContent = '⊘ Not connected';
    }
  } catch (err) {
    // Not connected
  }

  try {
    const uploads = await apiFetch('/api/bank/uploads');
    renderUploadList(uploads.uploads || []);
  } catch (err) {
    // No uploads
  }
}

async function connectRazorpay(e) {
  e.preventDefault();
  const keyId = document.getElementById('rz-key-id').value.trim();
  const keySecret = document.getElementById('rz-key-secret').value.trim();

  if (!keyId || !keySecret) {
    showToast('error', 'Please enter both Key ID and Key Secret');
    return;
  }

  showLoading('Connecting to Razorpay...');
  try {
    await apiFetch('/api/razorpay/connect', {
      method: 'POST',
      body: { key_id: keyId, key_secret: keySecret },
    });
    showToast('success', 'Razorpay connected successfully!');
    loadSettings();
  } catch (err) {
    showToast('error', err.message || 'Failed to connect');
  }
  hideLoading();
}

async function fetchRazorpayData() {
  showLoading('Fetching data from Razorpay...');
  try {
    const data = await apiFetch('/api/razorpay/fetch', { method: 'POST' });
    const el = document.getElementById('razorpay-fetch-result');
    el.innerHTML = `
      ✅ Fetched: <strong>${data.normalized.payments}</strong> payments,
      <strong>${data.normalized.settlements}</strong> settlements,
      <strong>${data.normalized.refunds}</strong> refunds
    `;
    el.style.color = 'var(--verified)';
    showToast('success', `Fetched ${data.normalized.payments} payments from Razorpay`);
  } catch (err) {
    showToast('error', err.message || 'Failed to fetch');
  }
  hideLoading();
}

async function uploadBankStatement(input) {
  const file = input.files[0];
  if (!file) return;

  const bankName = document.getElementById('bank-name-select').value;
  const formData = new FormData();
  formData.append('file', file);
  if (bankName) formData.append('bank_name', bankName);

  showLoading('Parsing bank statement...');
  try {
    const res = await fetch(`${API}/api/bank/upload`, {
      method: 'POST',
      headers: { Authorization: `Bearer ${authToken}` },
      body: formData,
    });
    const data = await res.json();
    if (!res.ok) throw new Error(data.error || 'Upload failed');

    let msg = `✅ ${data.bank_name}: ${data.total_rows} rows parsed`;
    if (data.razorpay_rows) msg += `, ${data.razorpay_rows} Razorpay`;
    if (data.other_gateway_rows) msg += `, ${data.other_gateway_rows} other gateways`;
    showToast('success', msg);
    loadSettings();
  } catch (err) {
    showToast('error', err.message || 'Upload failed');
  }
  hideLoading();
  input.value = '';
}

function renderUploadList(uploads) {
  const list = document.getElementById('upload-list');
  if (!list) return;
  if (!uploads.length) {
    list.innerHTML = '<li style="font-size:13px;color:var(--text-muted);padding:8px">No bank statements uploaded yet</li>';
    return;
  }
  list.innerHTML = uploads.map(u => `
    <li class="file-item">
      <div class="file-info">
        <span>📄</span>
        <div>
          <div style="font-weight:500">${u.filename}</div>
          <div style="font-size:11px;color:var(--text-muted)">
            ${u.bank_name} · ${u.row_count} rows · ${u.razorpay_rows} Razorpay
            ${u.other_gateway_rows ? ` · ${u.other_gateway_rows} other gateways` : ''}
          </div>
        </div>
      </div>
      <div style="font-size:11px;color:var(--text-muted)">${u.uploaded_at}</div>
    </li>
  `).join('');
}

// ================================================================ RECONCILIATION
async function runReconciliation(mode) {
  showLoading(mode === 'live' ? 'Reconciling live data...' : 'Running demo reconciliation...');
  try {
    const res = await apiFetch('/api/reconcile', {
      method: 'POST',
      body: { mode, seed: 42 },
    });
    showToast('success', `Reconciliation completed in ${res.wall_clock_seconds}s`);
    navigate('dashboard');
  } catch (err) {
    showToast('error', err.message || 'Reconciliation failed');
  }
  hideLoading();
}

// ================================================================ COPILOT
let copilotOpen = false;

function toggleCopilot() {
  copilotOpen = !copilotOpen;
  document.getElementById('copilot-panel').classList.toggle('open', copilotOpen);
  document.getElementById('copilot-overlay').classList.toggle('visible', copilotOpen);
  if (copilotOpen) document.getElementById('copilot-input').focus();
}

async function sendCopilotMessage() {
  const input = document.getElementById('copilot-input');
  const question = input.value.trim();
  if (!question) return;

  const messages = document.getElementById('copilot-messages');
  messages.innerHTML += `<div class="chat-msg user">${escapeHtml(question)}</div>`;
  input.value = '';
  messages.scrollTop = messages.scrollHeight;

  // Show typing indicator
  const typingId = 'typing-' + Date.now();
  messages.innerHTML += `<div class="chat-msg bot" id="${typingId}"><div class="spinner" style="width:16px;height:16px"></div></div>`;
  messages.scrollTop = messages.scrollHeight;

  try {
    const res = await apiFetch('/api/copilot/ask', {
      method: 'POST',
      body: { question },
    });
    document.getElementById(typingId).remove();
    messages.innerHTML += `<div class="chat-msg bot">${formatCopilotResponse(res.answer)}</div>`;
  } catch (err) {
    document.getElementById(typingId).remove();
    messages.innerHTML += `<div class="chat-msg bot" style="color:var(--unresolved)">❌ ${err.message || 'Failed to get response'}</div>`;
  }
  messages.scrollTop = messages.scrollHeight;
}

function formatCopilotResponse(text) {
  // Convert markdown-like formatting to HTML
  return text
    .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
    .replace(/`(.*?)`/g, '<code style="background:var(--bg-input);padding:1px 4px;border-radius:3px;font-size:11px">$1</code>')
    .replace(/^• /gm, '&bull; ')
    .replace(/\n/g, '<br>');
}

// ================================================================ DRAG & DROP
function setupDragDrop() {
  const area = document.getElementById('upload-area');
  if (!area) return;

  ['dragenter', 'dragover'].forEach(evt => {
    area.addEventListener(evt, (e) => {
      e.preventDefault();
      area.classList.add('dragover');
    });
  });
  ['dragleave', 'drop'].forEach(evt => {
    area.addEventListener(evt, (e) => {
      e.preventDefault();
      area.classList.remove('dragover');
    });
  });
  area.addEventListener('drop', (e) => {
    const file = e.dataTransfer?.files?.[0];
    if (file && file.name.endsWith('.csv')) {
      const input = document.getElementById('bank-file');
      const dt = new DataTransfer();
      dt.items.add(file);
      input.files = dt.files;
      uploadBankStatement(input);
    }
  });
}

// ================================================================ UTILITIES
async function apiFetch(url, options = {}) {
  const headers = { 'Content-Type': 'application/json' };
  if (authToken) headers['Authorization'] = `Bearer ${authToken}`;

  const config = {
    method: options.method || 'GET',
    headers,
  };

  if (options.body) {
    config.body = JSON.stringify(options.body);
  }

  const res = await fetch(`${API}${url}`, config);
  const data = await res.json();

  if (!res.ok) {
    if (res.status === 401) {
      authToken = null;
      localStorage.removeItem('afc_token');
      showPage('auth');
    }
    throw new Error(data.error || `Request failed (${res.status})`);
  }
  return data;
}

function animateNumber(elementId, target) {
  const el = document.getElementById(elementId);
  if (!el) return;

  const duration = 800;
  const start = performance.now();
  const initial = parseInt(el.textContent) || 0;

  function step(now) {
    const elapsed = now - start;
    const progress = Math.min(elapsed / duration, 1);
    const eased = 1 - Math.pow(1 - progress, 3); // ease-out cubic
    const current = Math.round(initial + (target - initial) * eased);
    el.textContent = current.toLocaleString('en-IN');
    if (progress < 1) requestAnimationFrame(step);
  }
  requestAnimationFrame(step);
}

function showToast(type, message) {
  const toast = document.getElementById('toast');
  toast.className = `toast ${type}`;
  toast.textContent = message;
  toast.classList.add('visible');
  setTimeout(() => toast.classList.remove('visible'), 4000);
}

function showLoading(text) {
  document.getElementById('loading-text').textContent = text || 'Processing...';
  document.getElementById('loading').style.display = 'flex';
}

function hideLoading() {
  document.getElementById('loading').style.display = 'none';
}

function escapeHtml(text) {
  const div = document.createElement('div');
  div.textContent = text;
  return div.innerHTML;
}

// Close modal on backdrop click
document.getElementById('txn-modal')?.addEventListener('click', (e) => {
  if (e.target.id === 'txn-modal') closeModal();
});

// Close modal on Escape
document.addEventListener('keydown', (e) => {
  if (e.key === 'Escape') {
    closeModal();
    if (copilotOpen) toggleCopilot();
  }
});
