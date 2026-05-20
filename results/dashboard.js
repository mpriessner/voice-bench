/* voice-bench dashboard — loads from window.__DATA__ set by data.js */
"use strict";

const TOOL_COUNTS = [1, 2, 3, 5, 10, 15, 20, 30, 40, 50];

// Maps short agent slugs to their actual model IDs for transparency in the heatmap.
const AGENT_MODEL_ID = {
  "claude-opus": "claude-opus-4-7",
  "claude-sonnet": "claude-sonnet-4-6",
  "gemini-3-flash": "gemini-3-flash-preview",
  "gemini-flash": "gemini-3.1-flash-lite",
  "gemini-live": "gemini-3.1-flash-live-preview",
  "gemini-live-v2": "gemini-3.1-flash-live-preview",
  "gemini-pro": "gemini-3.1-pro-preview",
  "gpt-4o": "gpt-4o",
  "gpt-5": "gpt-5",
  "gpt-text": "gpt-4o",
  "openai-realtime": "gpt-realtime (legacy)",
  "openai-realtime-v2": "gpt-realtime-2",
  "openai-realtime-swap": "gpt-realtime",
};

function agentDisplayLabel(agent) {
  const id = AGENT_MODEL_ID[agent];
  return id ? `${agent}<br><span class="hm-model-id">${id}</span>` : agent;
}
const FAILURE_COLORS = {
  pass: "#4ade80",
  wrong_tool: "#f87171",
  arg_mismatch: "#fbbf24",
  no_tool: "#64748b",
  false_positive: "#e11d48",
  timeout: "#a78bfa",
  provider_error: "#f97316",
  out_of_scope: "#38bdf8",
};

const AGENT_COLORS = [
  "#60a5fa", "#4ade80", "#f59e0b", "#c084fc",
  "#38bdf8", "#fb7185", "#a3e635", "#f97316",
  "#e879f9", "#34d399",
];

function agentColor(agent, allAgents) {
  const idx = allAgents.indexOf(agent);
  return AGENT_COLORS[idx % AGENT_COLORS.length];
}

function agentBadgeClass(agent) {
  if (agent.startsWith("gemini-live")) return "badge-gemini-live";
  if (agent.startsWith("openai-realtime")) return "badge-openai-realtime";
  if (agent.startsWith("claude")) return "badge-claude";
  if (agent.startsWith("gpt") || agent.startsWith("o3") || agent.startsWith("o4")) return "badge-gpt";
  if (agent.startsWith("gemini")) return "badge-gemini";
  return "";
}

function heatmapColor(acc) {
  if (acc === null) return "#1a2030";
  if (acc >= 0.9) return "#14532d";
  if (acc >= 0.7) return "#1a4030";
  if (acc >= 0.5) return "#3b3a1a";
  if (acc >= 0.3) return "#3b1a1a";
  return "#2a1010";
}

function median(arr) {
  if (!arr.length) return null;
  const s = [...arr].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 === 0 ? (s[m - 1] + s[m]) / 2 : s[m];
}

// ─── Filter state ────────────────────────────────────────────────────────────
let allData = [];
let filteredData = [];
let chartAccuracy = null;
let chartLatency = null;
let chartFailure = null;

function applyFilters() {
  const agent = document.getElementById("filter-agent").value;
  const manifest = document.getElementById("filter-manifest").value;
  const kind = document.getElementById("filter-kind").value;
  const bmMode = document.getElementById("filter-bm-mode")?.value || "";
  filteredData = allData.filter(r =>
    (!agent || r.agent === agent) &&
    (!manifest || r.manifest === manifest) &&
    (!kind || r.model_kind === kind) &&
    (!bmMode || (r.benchmark_mode || "needle") === bmMode)
  );
  render();
}

// ─── Populate filter dropdowns ────────────────────────────────────────────────
function populateFilters(data) {
  const agents = [...new Set(data.map(r => r.agent))].sort();
  const manifests = [...new Set(data.map(r => r.manifest).filter(Boolean))].sort();

  const agentSel = document.getElementById("filter-agent");
  agents.forEach(a => {
    const opt = document.createElement("option");
    opt.value = a; opt.textContent = a;
    agentSel.appendChild(opt);
  });

  const manifestSel = document.getElementById("filter-manifest");
  manifests.forEach(m => {
    const opt = document.createElement("option");
    opt.value = m; opt.textContent = m;
    manifestSel.appendChild(opt);
  });
}

// ─── Aggregate runs ──────────────────────────────────────────────────────────
function aggregateRuns(data) {
  // Group by (agent, tool_count) across all matching rows.
  // Returns [{ agent, tool_count, accuracy, latency_ms, count }]
  const buckets = {};
  for (const r of data) {
    const k = `${r.agent}|${r.tool_count}`;
    if (!buckets[k]) buckets[k] = { agent: r.agent, tool_count: r.tool_count, passed: 0, total: 0, latencies: [] };
    const b = buckets[k];
    b.total++;
    if (r.passed) b.passed++;
    const lat = r.model_kind === "text" ? r.ttf_request_to_call_ms : r.ttf_tool_ms;
    if (lat != null) b.latencies.push(lat);
  }
  return Object.values(buckets).map(b => ({
    agent: b.agent, tool_count: b.tool_count,
    accuracy: b.total ? b.passed / b.total : null,
    latency_ms: median(b.latencies),
    count: b.total,
  }));
}

// ─── Stats row ───────────────────────────────────────────────────────────────
function renderStats(data) {
  const total = data.length;
  const passed = data.filter(r => r.passed).length;
  const agents = new Set(data.map(r => r.agent)).size;
  const latencies = data
    .map(r => r.model_kind === "text" ? r.ttf_request_to_call_ms : r.ttf_tool_ms)
    .filter(v => v != null);
  const p50 = median(latencies);

  const el = document.getElementById("stat-row");
  el.innerHTML = `
    <div class="stat"><div class="stat-label">Total turns</div><div class="stat-val">${total}</div></div>
    <div class="stat"><div class="stat-label">Overall accuracy</div><div class="stat-val ${passed/total >= 0.7 ? 'pass' : 'fail'}">${total ? (passed/total*100).toFixed(1) : 0}%</div><div class="stat-sub">${passed}/${total}</div></div>
    <div class="stat"><div class="stat-label">Agents benchmarked</div><div class="stat-val">${agents}</div></div>
    <div class="stat"><div class="stat-label">Latency p50</div><div class="stat-val">${p50 != null ? p50.toFixed(0)+'ms' : '—'}</div></div>
  `;
}

// ─── Heatmap ─────────────────────────────────────────────────────────────────
function renderHeatmap(data) {
  const agents = [...new Set(data.map(r => r.agent))].sort();
  const counts = [...new Set(data.map(r => r.tool_count).filter(v => v != null))].sort((a, b) => a - b);
  if (!agents.length || !counts.length) {
    document.getElementById("heatmap-wrap").innerHTML = "<p class='dimmed'>No data.</p>";
    return;
  }

  // Build accuracy map: agent → toolCount → accuracy
  const acc = {};
  for (const r of data) {
    if (r.tool_count == null) continue;
    if (!acc[r.agent]) acc[r.agent] = {};
    if (!acc[r.agent][r.tool_count]) acc[r.agent][r.tool_count] = { p: 0, t: 0 };
    acc[r.agent][r.tool_count].t++;
    if (r.passed) acc[r.agent][r.tool_count].p++;
  }

  let html = '<table class="heatmap-table"><thead><tr><th></th>';
  counts.forEach(c => { html += `<th>${c}t</th>`; });
  html += '</tr></thead><tbody>';

  for (const ag of agents) {
    html += `<tr><td class="hm-agent-label">${agentDisplayLabel(ag)}</td>`;
    for (const c of counts) {
      const bucket = acc[ag]?.[c];
      if (!bucket) { html += '<td style="background:#1a2030;color:#475569">—</td>'; continue; }
      const a = bucket.p / bucket.t;
      const bg = heatmapColor(a);
      html += `<td style="background:${bg};color:#e2e8f0" title="${bucket.p}/${bucket.t} turns">${(a*100).toFixed(0)}%</td>`;
    }
    html += '</tr>';
  }
  html += '</tbody></table>';
  document.getElementById("heatmap-wrap").innerHTML = html;
}

// ─── Accuracy chart ──────────────────────────────────────────────────────────
function renderAccuracyChart(data) {
  const agg = aggregateRuns(data);
  const agents = [...new Set(agg.map(r => r.agent))].sort();
  const counts = [...new Set(agg.map(r => r.tool_count).filter(v => v != null))].sort((a, b) => a - b);

  const datasets = agents.map((ag, i) => {
    const pts = counts.map(c => {
      const match = agg.find(r => r.agent === ag && r.tool_count === c);
      return match ? match.accuracy * 100 : null;
    });
    return {
      label: ag,
      data: pts,
      borderColor: agentColor(ag, agents),
      backgroundColor: agentColor(ag, agents) + "33",
      pointRadius: 4,
      tension: 0.3,
      spanGaps: true,
    };
  });

  const ctx = document.getElementById("accuracy-chart").getContext("2d");
  if (chartAccuracy) chartAccuracy.destroy();
  chartAccuracy = new Chart(ctx, {
    type: "line",
    data: { labels: counts.map(c => c + "t"), datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: "#94a3b8", font: { size: 11 } } } },
      scales: {
        x: { ticks: { color: "#64748b" }, grid: { color: "#1e2535" } },
        y: { min: 0, max: 100, ticks: { color: "#64748b", callback: v => v + "%" }, grid: { color: "#1e2535" } }
      }
    }
  });
}

// ─── Latency chart ───────────────────────────────────────────────────────────
function renderLatencyChart(data) {
  const agg = aggregateRuns(data);
  const agents = [...new Set(agg.map(r => r.agent))].sort();
  const counts = [...new Set(agg.map(r => r.tool_count).filter(v => v != null))].sort((a, b) => a - b);

  const datasets = agents.map((ag, i) => {
    const pts = counts.map(c => {
      const match = agg.find(r => r.agent === ag && r.tool_count === c);
      return match ? match.latency_ms : null;
    });
    return {
      label: ag,
      data: pts,
      borderColor: agentColor(ag, agents),
      backgroundColor: agentColor(ag, agents) + "33",
      pointRadius: 4,
      tension: 0.3,
      spanGaps: true,
    };
  });

  const ctx = document.getElementById("latency-chart").getContext("2d");
  if (chartLatency) chartLatency.destroy();
  chartLatency = new Chart(ctx, {
    type: "line",
    data: { labels: counts.map(c => c + "t"), datasets },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: { legend: { labels: { color: "#94a3b8", font: { size: 11 } } } },
      scales: {
        x: { ticks: { color: "#64748b" }, grid: { color: "#1e2535" } },
        y: { ticks: { color: "#64748b", callback: v => v + "ms" }, grid: { color: "#1e2535" } }
      }
    }
  });
}

// ─── Failure breakdown chart ─────────────────────────────────────────────────
function renderFailureChart(data) {
  const counts = {};
  for (const r of data) {
    counts[r.failure_kind] = (counts[r.failure_kind] || 0) + 1;
  }
  const labels = Object.keys(counts);
  const values = labels.map(k => counts[k]);
  const colors = labels.map(k => FAILURE_COLORS[k] || "#475569");

  const ctx = document.getElementById("failure-chart").getContext("2d");
  if (chartFailure) chartFailure.destroy();
  chartFailure = new Chart(ctx, {
    type: "bar",
    data: {
      labels,
      datasets: [{
        data: values,
        backgroundColor: colors,
        borderRadius: 4,
      }]
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      indexAxis: "y",
      plugins: { legend: { display: false } },
      scales: {
        x: { ticks: { color: "#64748b" }, grid: { color: "#1e2535" } },
        y: { ticks: { color: "#94a3b8" }, grid: { color: "#1e2535" } }
      }
    }
  });
}

// ─── Turn explorer ───────────────────────────────────────────────────────────
const PAGE_SIZE = 30;
let explorerPage = 0;
let explorerRows = [];

function explorerFilter() {
  const q = document.getElementById("explorer-filter").value.toLowerCase();
  const result = document.getElementById("explorer-result").value;
  explorerRows = filteredData.filter(r => {
    const matchText = !q ||
      (r.prompt_text || "").toLowerCase().includes(q) ||
      (r.expected_tool || "").toLowerCase().includes(q) ||
      (r.called_tool || "").toLowerCase().includes(q);
    const matchResult = !result ||
      (result === "pass" && r.passed) ||
      (result === "fail" && !r.passed);
    return matchText && matchResult;
  });
  explorerPage = 0;
  renderExplorerPage();
}

function renderExplorerPage() {
  const start = explorerPage * PAGE_SIZE;
  const pageRows = explorerRows.slice(start, start + PAGE_SIZE);
  const tbody = document.getElementById("detail-body");

  tbody.innerHTML = pageRows.map(r => {
    const lat = r.model_kind === "text" ? r.ttf_request_to_call_ms : r.ttf_tool_ms;
    const latStr = lat != null ? lat + "ms" : "—";
    const passClass = r.passed ? "pass" : "fail";
    const passLabel = r.passed ? "✓" : "✗";
    const fkColor = FAILURE_COLORS[r.failure_kind] || "#475569";
    const badgeClass = agentBadgeClass(r.agent);
    const argStr = r.arg_score != null ? r.arg_score.toFixed(2) : "—";
    const calledTool = r.called_tool || '<span class="dimmed">—</span>';
    const wrongTool = r.called_tool && r.called_tool !== r.expected_tool
      ? `<span class="fail"> (${r.called_tool})</span>` : "";
    return `<tr>
      <td><span class="badge ${badgeClass}">${r.agent}</span></td>
      <td>${r.tool_count ?? "—"}</td>
      <td style="max-width:260px;word-break:break-word">${r.prompt_text}</td>
      <td>${r.expected_tool || '<span class="dimmed">—</span>'}</td>
      <td>${r.called_tool || '<span class="dimmed">—</span>'}${wrongTool}</td>
      <td>${argStr}</td>
      <td>${latStr}</td>
      <td class="${passClass}" title="${r.failure_kind}" style="color:${fkColor}">${passLabel} <span style="font-size:0.75rem">${r.failure_kind}</span></td>
    </tr>`;
  }).join("");

  // Pagination
  const totalPages = Math.ceil(explorerRows.length / PAGE_SIZE);
  const pag = document.getElementById("detail-pagination");
  pag.innerHTML = `
    <button onclick="prevPage()" ${explorerPage === 0 ? "disabled" : ""}>‹ Prev</button>
    <span>Page ${explorerPage + 1} / ${totalPages || 1} (${explorerRows.length} rows)</span>
    <button onclick="nextPage()" ${explorerPage >= totalPages - 1 ? "disabled" : ""}>Next ›</button>
  `;
}

function prevPage() { if (explorerPage > 0) { explorerPage--; renderExplorerPage(); } }
function nextPage() {
  if ((explorerPage + 1) * PAGE_SIZE < explorerRows.length) { explorerPage++; renderExplorerPage(); }
}

// ─── Subtitle ────────────────────────────────────────────────────────────────
function renderSubtitle(data) {
  const agents = new Set(data.map(r => r.agent)).size;
  const runs = new Set(data.map(r => r.run_id)).size;
  document.getElementById("subtitle").textContent =
    `${runs} run${runs !== 1 ? "s" : ""} · ${agents} agent${agents !== 1 ? "s" : ""} · ${data.length} turns`;
}

// ─── Master render ────────────────────────────────────────────────────────────
function render() {
  renderSubtitle(filteredData);
  renderStats(filteredData);
  renderHeatmap(filteredData);
  renderAccuracyChart(filteredData);
  renderLatencyChart(filteredData);
  renderFailureChart(filteredData);
  explorerRows = [...filteredData];
  explorerPage = 0;
  renderExplorerPage();
}

// ─── Init ────────────────────────────────────────────────────────────────────
document.addEventListener("DOMContentLoaded", () => {
  if (!window.__DATA__) {
    document.body.innerHTML = "<p style='padding:40px;color:#f87171'>data.js not found — run: uv run python scripts/build_dashboard.py</p>";
    return;
  }

  allData = window.__DATA__;
  filteredData = allData;

  populateFilters(allData);
  render();

  document.getElementById("filter-agent").addEventListener("change", applyFilters);
  document.getElementById("filter-manifest").addEventListener("change", applyFilters);
  document.getElementById("filter-kind").addEventListener("change", applyFilters);
  document.getElementById("filter-bm-mode").addEventListener("change", applyFilters);
  document.getElementById("explorer-filter").addEventListener("input", explorerFilter);
  document.getElementById("explorer-result").addEventListener("change", explorerFilter);
});
