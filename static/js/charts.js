/* ================================================================
   Chart.js configurations for the dashboard
   ================================================================ */

const CHART_COLORS = {
  verified: '#34d399',
  explained: '#60a5fa',
  awaiting: '#fbbf24',
  humanReview: '#fb923c',
  unresolved: '#f87171',
  accent: '#818cf8',
  muted: '#64748b',
  grid: 'rgba(255,255,255,0.04)',
};

// Chart.js global defaults for dark theme
Chart.defaults.color = '#94a3b8';
Chart.defaults.borderColor = 'rgba(255,255,255,0.06)';
Chart.defaults.font.family = "'Inter', sans-serif";
Chart.defaults.font.size = 12;
Chart.defaults.plugins.legend.labels.usePointStyle = true;
Chart.defaults.plugins.legend.labels.pointStyleWidth = 10;
Chart.defaults.plugins.legend.labels.padding = 16;

let chartDonut = null;
let chartConfidence = null;
let chartMoney = null;

function destroyCharts() {
  if (chartDonut) { chartDonut.destroy(); chartDonut = null; }
  if (chartConfidence) { chartConfidence.destroy(); chartConfidence = null; }
  if (chartMoney) { chartMoney.destroy(); chartMoney = null; }
}

function renderStateDonut(stateCounts) {
  const ctx = document.getElementById('chart-donut');
  if (!ctx) return;

  const labels = ['Verified', 'Explained', 'Awaiting Bank', 'Human Review', 'Unresolved'];
  const data = [
    stateCounts.VERIFIED || 0,
    stateCounts.EXPLAINED || 0,
    stateCounts.AWAITING_BANK || 0,
    stateCounts.HUMAN_REVIEW || 0,
    stateCounts.UNRESOLVED || 0,
  ];
  const colors = [
    CHART_COLORS.verified,
    CHART_COLORS.explained,
    CHART_COLORS.awaiting,
    CHART_COLORS.humanReview,
    CHART_COLORS.unresolved,
  ];

  if (chartDonut) chartDonut.destroy();
  chartDonut = new Chart(ctx, {
    type: 'doughnut',
    data: {
      labels,
      datasets: [{
        data,
        backgroundColor: colors,
        borderColor: '#1a1f35',
        borderWidth: 3,
        hoverBorderWidth: 0,
        hoverOffset: 8,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      cutout: '65%',
      plugins: {
        legend: {
          position: 'right',
          labels: { font: { size: 11 }, padding: 12 },
        },
        tooltip: {
          backgroundColor: '#1a1f35',
          borderColor: 'rgba(255,255,255,0.1)',
          borderWidth: 1,
          cornerRadius: 8,
          padding: 12,
          callbacks: {
            label: (ctx) => {
              const total = ctx.dataset.data.reduce((a, b) => a + b, 0);
              const pct = total ? ((ctx.parsed / total) * 100).toFixed(1) : 0;
              return ` ${ctx.label}: ${ctx.parsed} (${pct}%)`;
            },
          },
        },
      },
    },
  });
}

function renderConfidenceChart(decisions) {
  const ctx = document.getElementById('chart-confidence');
  if (!ctx) return;

  // Bin confidence scores
  const bins = { '0.0-0.2': 0, '0.2-0.4': 0, '0.4-0.6': 0, '0.6-0.8': 0, '0.8-1.0': 0 };
  for (const d of decisions) {
    const score = d.confidence?.score ?? 0;
    if (score < 0.2) bins['0.0-0.2']++;
    else if (score < 0.4) bins['0.2-0.4']++;
    else if (score < 0.6) bins['0.4-0.6']++;
    else if (score < 0.8) bins['0.6-0.8']++;
    else bins['0.8-1.0']++;
  }

  if (chartConfidence) chartConfidence.destroy();
  chartConfidence = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: Object.keys(bins),
      datasets: [{
        label: 'Transactions',
        data: Object.values(bins),
        backgroundColor: [
          'rgba(248, 113, 113, 0.6)',
          'rgba(251, 146, 60, 0.6)',
          'rgba(251, 191, 36, 0.6)',
          'rgba(96, 165, 250, 0.6)',
          'rgba(52, 211, 153, 0.6)',
        ],
        borderColor: [
          '#f87171',
          '#fb923c',
          '#fbbf24',
          '#60a5fa',
          '#34d399',
        ],
        borderWidth: 1,
        borderRadius: 6,
        borderSkipped: false,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#1a1f35',
          borderColor: 'rgba(255,255,255,0.1)',
          borderWidth: 1,
          cornerRadius: 8,
          padding: 12,
        },
      },
      scales: {
        x: {
          grid: { display: false },
          ticks: { font: { size: 10 } },
        },
        y: {
          grid: { color: CHART_COLORS.grid },
          beginAtZero: true,
          ticks: { stepSize: 1 },
        },
      },
    },
  });
}

function renderMoneyChart(metrics) {
  const ctx = document.getElementById('chart-money');
  if (!ctx) return;

  const rupees = metrics?.rupees || {};
  const labels = ['Auto Correct', 'False Positive Cost', 'Human Review', 'Unresolved'];
  const data = [
    (rupees.auto_reconciled_correct || 0) / 100,
    (rupees.auto_reconciled_wrong_cost_of_false_positives || 0) / 100,
    (rupees.sent_to_human_review || 0) / 100,
    (rupees.unresolved || 0) / 100,
  ];

  // If no rupees data (live mode), use state counts as fallback
  const hasData = data.some(v => v > 0);

  if (chartMoney) chartMoney.destroy();
  chartMoney = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: hasData ? labels : ['Verified', 'Explained', 'Awaiting', 'Review', 'Unresolved'],
      datasets: [{
        label: hasData ? 'Amount (₹)' : 'Count',
        data: hasData ? data : [
          metrics?.state_counts?.VERIFIED || 0,
          metrics?.state_counts?.EXPLAINED || 0,
          metrics?.state_counts?.AWAITING_BANK || 0,
          metrics?.state_counts?.HUMAN_REVIEW || 0,
          metrics?.state_counts?.UNRESOLVED || 0,
        ],
        backgroundColor: [
          'rgba(52, 211, 153, 0.6)',
          'rgba(248, 113, 113, 0.15)',
          'rgba(251, 191, 36, 0.6)',
          'rgba(248, 113, 113, 0.6)',
          hasData ? 'rgba(248,113,113,0.6)' : 'rgba(248,113,113,0.6)',
        ],
        borderColor: [
          '#34d399',
          '#f87171',
          '#fbbf24',
          '#f87171',
          '#f87171',
        ],
        borderWidth: 1,
        borderRadius: 6,
        borderSkipped: false,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      indexAxis: 'y',
      plugins: {
        legend: { display: false },
        tooltip: {
          backgroundColor: '#1a1f35',
          borderColor: 'rgba(255,255,255,0.1)',
          borderWidth: 1,
          cornerRadius: 8,
          padding: 12,
          callbacks: {
            label: (ctx) => hasData
              ? ` ₹${ctx.parsed.x.toLocaleString('en-IN', { maximumFractionDigits: 0 })}`
              : ` ${ctx.parsed.x} transactions`,
          },
        },
      },
      scales: {
        y: {
          grid: { display: false },
          ticks: { font: { size: 10 } },
        },
        x: {
          grid: { color: CHART_COLORS.grid },
          beginAtZero: true,
          ticks: {
            callback: (v) => hasData ? `₹${(v/1000).toFixed(0)}k` : v,
          },
        },
      },
    },
  });
}

function renderGauge(containerId, value, maxVal, color) {
  const container = document.getElementById(containerId);
  if (!container) return;

  const pct = maxVal > 0 ? Math.min(value / maxVal, 1) : 0;
  const circumference = 2 * Math.PI * 42;
  const dashoffset = circumference * (1 - pct);

  container.innerHTML = `
    <svg width="100" height="100" viewBox="0 0 100 100">
      <circle cx="50" cy="50" r="42" fill="none" stroke="rgba(255,255,255,0.06)" stroke-width="6"/>
      <circle cx="50" cy="50" r="42" fill="none" stroke="${color}" stroke-width="6"
        stroke-linecap="round"
        stroke-dasharray="${circumference}"
        stroke-dashoffset="${dashoffset}"
        style="transition: stroke-dashoffset 1s ease-out"/>
    </svg>
    <div class="gauge-value num" style="color:${color}">${(pct * 100).toFixed(1)}%</div>
  `;
}
