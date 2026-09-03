/* ================================================================
   Transaction Money Trail Flow Visualization
   ================================================================ */

function renderTransactionFlow(flow, containerId) {
  const container = document.getElementById(containerId);
  if (!container) return;

  if (!flow || !flow.payment) {
    container.innerHTML = `
      <div class="empty-state" style="padding:20px">
        <p style="font-size:13px;color:var(--text-muted)">No flow data available for this transaction.</p>
      </div>`;
    return;
  }

  const payment = flow.payment;
  const settlement = flow.settlement;
  const ledger = flow.ledger;
  const state = flow.state;

  // Determine node states
  const paymentOk = !!payment;
  const settlementOk = !!settlement;
  const settlementUtr = settlement?.utr;
  const ledgerOk = !!ledger;

  const stateClass = {
    VERIFIED: 'match',
    EXPLAINED: 'match',
    AWAITING_BANK: 'warning',
    HUMAN_REVIEW: 'warning',
    UNRESOLVED: 'mismatch',
  }[state] || 'mismatch';

  const bankStatus = state === 'AWAITING_BANK' ? 'missing' :
                     state === 'UNRESOLVED' && !settlement ? 'missing' : stateClass;

  const paise = (p) => {
    if (p == null) return '—';
    const sign = p < 0 ? '−' : '';
    const abs = Math.abs(p / 100);
    return `${sign}₹${abs.toLocaleString('en-IN', { minimumFractionDigits: 2, maximumFractionDigits: 2 })}`;
  };

  container.innerHTML = `
    <div class="flow-nodes">
      <!-- Payment Node -->
      <div class="flow-node">
        <div class="flow-node-circle match">💳</div>
        <div class="flow-node-label">Payment</div>
        <div class="flow-node-value">${paise(payment.gross_paise)}</div>
        <div style="font-size:10px;color:var(--text-muted)" class="mono">${payment.payment_id}</div>
      </div>

      <div class="flow-arrow ${paymentOk && settlementOk ? 'match' : 'mismatch'}"></div>

      <!-- Settlement Node -->
      <div class="flow-node">
        <div class="flow-node-circle ${settlementOk ? stateClass : 'missing'}">📋</div>
        <div class="flow-node-label">Settlement</div>
        <div class="flow-node-value">${settlement ? settlement.settlement_id : 'Missing'}</div>
        <div style="font-size:10px;color:var(--text-muted)" class="mono">
          ${settlement ? (settlement.utr || 'No UTR') : '—'}
        </div>
      </div>

      <div class="flow-arrow ${bankStatus === 'missing' ? '' : stateClass}"></div>

      <!-- Bank Node -->
      <div class="flow-node">
        <div class="flow-node-circle ${bankStatus}">🏦</div>
        <div class="flow-node-label">Bank Credit</div>
        <div class="flow-node-value">${bankStatus === 'missing' ? 'Pending' : paise(payment.gross_paise)}</div>
        <div style="font-size:10px;color:var(--text-muted)" class="mono">
          ${settlement?.due_on ? `Due: ${settlement.due_on}` : '—'}
        </div>
      </div>

      <div class="flow-arrow ${ledgerOk ? 'match' : 'mismatch'}"></div>

      <!-- Ledger Node -->
      <div class="flow-node">
        <div class="flow-node-circle ${ledgerOk ? 'match' : 'mismatch'}">📒</div>
        <div class="flow-node-label">Ledger</div>
        <div class="flow-node-value">${ledger ? paise(ledger.amount_paise) : 'Missing'}</div>
        <div style="font-size:10px;color:var(--text-muted)" class="mono">
          ${ledger ? ledger.ledger_id : '—'}
        </div>
      </div>
    </div>

    <!-- Flow summary -->
    <div style="text-align:center;margin-top:16px;padding-top:16px;border-top:1px solid var(--border-color)">
      <span class="badge badge-${state.toLowerCase().replace('_','-')}" style="font-size:12px;padding:6px 14px">
        ${state}
      </span>
      <span style="margin-left:12px;font-size:12px;color:var(--text-muted)">
        Rule ${flow.rule} · ${flow.unit_kind}
      </span>
    </div>
  `;
}

function renderFlowForTransaction(unitId) {
  const flows = window._currentFlows || [];
  const flow = flows.find(f => f.unit_id === unitId);

  document.getElementById('flow-empty').style.display = 'none';
  document.getElementById('flow-content').style.display = 'block';
  document.getElementById('flow-title').textContent = `Flow for ${unitId}`;

  renderTransactionFlow(flow, 'flow-content');
}
