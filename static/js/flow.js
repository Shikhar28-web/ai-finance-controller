/* ================================================================
   Transaction Money Trail Flow Visualization
   ================================================================ */

function renderTransactionFlow(flow, decomp, containerId) {
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
    <div class="flow-container-inner" style="display:flex;align-items:center;justify-content:center;gap:16px;padding:32px 0;">
      <!-- Payment Node -->
      <div class="flow-node">
        <div class="flow-icon match">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="1" y="4" width="22" height="16" rx="2" ry="2"></rect><line x1="1" y1="10" x2="23" y2="10"></line></svg>
        </div>
        <div class="flow-details">
          <div class="flow-title">Payment</div>
          <div class="flow-amount">${paise(payment.gross_paise)}</div>
          <div class="flow-sub mono">${payment.payment_id}</div>
        </div>
      </div>

      <div class="flow-connector ${paymentOk && settlementOk ? 'match' : 'mismatch'}"></div>

      <!-- Settlement Node -->
      <div class="flow-node">
        <div class="flow-icon ${settlementOk ? stateClass : 'missing'}">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"></path><polyline points="14 2 14 8 20 8"></polyline><line x1="16" y1="13" x2="8" y2="13"></line><line x1="16" y1="17" x2="8" y2="17"></line><polyline points="10 9 9 9 8 9"></polyline></svg>
        </div>
        <div class="flow-details">
          <div class="flow-title">Settled Amount</div>
          <div class="flow-amount">${settlementOk && decomp ? paise(decomp.expected_paise) : 'Missing'}</div>
          <div class="flow-sub mono" title="${settlement?.utr || ''}">${settlement ? settlement.settlement_id : '—'}</div>
        </div>
      </div>

      <div class="flow-connector ${bankStatus === 'missing' ? '' : stateClass}"></div>

      <!-- Bank Node -->
      <div class="flow-node">
        <div class="flow-icon ${bankStatus}">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2" ry="2"></rect><line x1="3" y1="9" x2="21" y2="9"></line><line x1="9" y1="21" x2="9" y2="21"></line></svg>
        </div>
        <div class="flow-details">
          <div class="flow-title">Bank Credit</div>
          <div class="flow-amount">${bankStatus === 'missing' ? 'Pending' : (decomp?.actual_paise != null ? paise(decomp.actual_paise) : '—')}</div>
          <div class="flow-sub mono">${settlement?.utr ? settlement.utr : (settlement?.due_on ? `Due: ${settlement.due_on}` : '—')}</div>
        </div>
      </div>

      <div class="flow-connector ${ledgerOk ? 'match' : 'mismatch'}"></div>

      <!-- Ledger Node -->
      <div class="flow-node">
        <div class="flow-icon ${ledgerOk ? 'match' : 'mismatch'}">
          <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20"></path><path d="M6.5 2H20v20H6.5A2.5 2.5 0 0 1 4 19.5v-15A2.5 2.5 0 0 1 6.5 2z"></path></svg>
        </div>
        <div class="flow-details">
          <div class="flow-title">Ledger Record</div>
          <div class="flow-amount">${ledger ? paise(ledger.amount_paise) : 'Missing'}</div>
          <div class="flow-sub mono">${ledger ? ledger.ledger_id : '—'}</div>
        </div>
      </div>
    </div>

    <!-- Flow summary -->
    <div style="text-align:center;margin-top:16px;padding-top:24px;border-top:1px solid var(--border-strong);display:flex;align-items:center;justify-content:center;gap:12px">
      <span class="badge badge-${state.toLowerCase().replace('_','-')}" style="font-size:13px;padding:8px 16px;letter-spacing:0.02em">
        ${state}
      </span>
      <span style="font-size:13px;color:var(--text-muted)">
        Rule ${flow.rule} · ${flow.unit_kind.charAt(0).toUpperCase() + flow.unit_kind.slice(1)} Routing
      </span>
    </div>
  `;
}

function renderFlowForTransaction(unitId) {
  const flows = window._currentFlows || [];
  const flow = flows.find(f => f.unit_id === unitId);
  const decompositions = (window._currentResults || currentResults)?.decompositions || [];
  let decomp = null;
  if (flow?.settlement?.settlement_id) {
    decomp = decompositions.find(d => d.settlement_id === flow.settlement.settlement_id);
  }

  document.getElementById('flow-empty').style.display = 'none';
  document.getElementById('flow-content').style.display = 'block';
  document.getElementById('flow-title').textContent = `Flow for ${unitId}`;

  renderTransactionFlow(flow, decomp, 'flow-content');
}
