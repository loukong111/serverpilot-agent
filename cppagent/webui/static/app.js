const state = {
  activeJobId: null,
  lastJson: {},
  lastMarkdown: "",
  talkScripts: {},
};

const $ = (id) => document.getElementById(id);
const delay = (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));

function setStatus(text) {
  $("statusText").textContent = text;
}

function setBusy(busy) {
  ["analyzeBtn", "agentBtn", "diagnoseBtn", "astBtn", "askBtn", "clearBtn"].forEach((id) => {
    $(id).disabled = busy;
  });
}

function escapeHtml(value) {
  return String(value)
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function inlineMarkdown(value) {
  return escapeHtml(value)
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
    .replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noreferrer">$1</a>');
}

function renderMarkdown(markdown) {
  const lines = String(markdown || "").split("\n");
  const html = [];
  let listType = "";
  let inCode = false;
  let codeLines = [];

  function closeList() {
    if (listType) {
      html.push(`</${listType}>`);
      listType = "";
    }
  }

  function openList(type) {
    if (listType === type) return;
    closeList();
    listType = type;
    html.push(`<${type}>`);
  }

  for (const line of lines) {
    if (line.startsWith("```")) {
      if (inCode) {
        html.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
        codeLines = [];
        inCode = false;
      } else {
        closeList();
        inCode = true;
      }
      continue;
    }
    if (inCode) {
      codeLines.push(line);
      continue;
    }
    if (line.startsWith("# ")) {
      closeList();
      html.push(`<h1>${inlineMarkdown(line.slice(2))}</h1>`);
    } else if (line.startsWith("## ")) {
      closeList();
      html.push(`<h2>${inlineMarkdown(line.slice(3))}</h2>`);
    } else if (line.startsWith("### ")) {
      closeList();
      html.push(`<h3>${inlineMarkdown(line.slice(4))}</h3>`);
    } else if (line.startsWith("- ")) {
      openList("ul");
      html.push(`<li>${inlineMarkdown(line.slice(2))}</li>`);
    } else if (/^\d+\.\s/.test(line)) {
      openList("ol");
      html.push(`<li>${inlineMarkdown(line.replace(/^\d+\.\s/, ""))}</li>`);
    } else if (line.startsWith("> ")) {
      closeList();
      html.push(`<blockquote>${inlineMarkdown(line.slice(2))}</blockquote>`);
    } else if (line.trim() === "") {
      closeList();
    } else {
      closeList();
      html.push(`<p>${inlineMarkdown(line)}</p>`);
    }
  }
  closeList();
  if (inCode) {
    html.push(`<pre><code>${escapeHtml(codeLines.join("\n"))}</code></pre>`);
  }
  return html.join("\n");
}

function showOutputView(viewId) {
  document.querySelectorAll(".output-view").forEach((view) => {
    const active = view.id === viewId;
    view.classList.toggle("active", active);
    view.hidden = !active;
  });
  document.querySelectorAll(".output-tab").forEach((tab) => {
    const active = tab.dataset.view === viewId;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });
}

function showWorkflow(panelId) {
  document.querySelectorAll(".workflow-panel").forEach((panel) => {
    const active = panel.id === panelId;
    panel.classList.toggle("active", active);
    panel.hidden = !active;
  });
  document.querySelectorAll(".workflow-tab").forEach((tab) => {
    const active = tab.dataset.workflow === panelId;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
  });
}

function setReport(markdown) {
  state.lastMarkdown = String(markdown || "");
  $("reportOutput").classList.remove("empty-state");
  $("reportOutput").innerHTML = renderMarkdown(state.lastMarkdown);
  showOutputView("reportView");
}

function stripInterviewSection(markdown) {
  const lines = String(markdown || "").split("\n");
  const start = lines.findIndex((line) => /^##\s+.*面试讲法/.test(line));
  if (start < 0) return markdown;
  const relativeEnd = lines.slice(start + 1).findIndex((line) => /^##\s+/.test(line));
  const end = relativeEnd < 0 ? lines.length : start + 1 + relativeEnd;
  return [...lines.slice(0, start), ...lines.slice(end)].join("\n").trim();
}

function setJson(data) {
  state.lastJson = data || {};
  $("jsonOutput").textContent = JSON.stringify(state.lastJson, null, 2);
}

function renderTalkScripts(scripts) {
  state.talkScripts = scripts || {};
  const panel = $("talkPanel");
  const duration = $("talkDuration");
  if (!Object.keys(state.talkScripts).length) {
    panel.hidden = true;
    $("talkOutput").textContent = "";
    return;
  }
  panel.hidden = false;
  duration.value = state.talkScripts["30s"] ? "30s" : Object.keys(state.talkScripts)[0];
  $("talkOutput").textContent = state.talkScripts[duration.value] || "";
}

function renderTrace(trace) {
  const steps = trace?.steps || [];
  if (!steps.length) {
    $("traceOutput").classList.add("empty-state");
    $("traceOutput").textContent = "等待 Agent Trace";
    return;
  }
  $("traceOutput").classList.remove("empty-state");
  $("traceOutput").innerHTML = steps
    .map((step) => {
      const statusClass = step.success ? "ok" : "fail";
      const statusLabel = step.success ? "OK" : "FAIL";
      return `
        <section class="trace-step">
          <span class="status-badge ${statusClass}">${statusLabel}</span>
          <div>
            <p class="trace-title">${escapeHtml(step.index)}. ${escapeHtml(step.tool)}</p>
            <p class="trace-note">${escapeHtml(step.observation || "")}</p>
          </div>
          <div class="duration">${escapeHtml(step.duration_ms ?? 0)} ms</div>
        </section>
      `;
    })
    .join("");
}

async function requestJson(url, options = {}) {
  const response = await fetch(url, options);
  const text = await response.text();
  let data;
  try {
    data = JSON.parse(text);
  } catch (_error) {
    throw new Error(text || `HTTP ${response.status}`);
  }
  if (!response.ok || !data.ok) {
    throw new Error(data.error || data.stderr || "请求失败");
  }
  return data;
}

function postJson(url, payload) {
  return requestJson(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
}

function projectPayload() {
  const projectPath = $("projectPath").value.trim();
  if (!projectPath) throw new Error("请输入项目路径");
  return { project_path: projectPath };
}

function llmPayload() {
  return {
    model: $("modelName").value.trim(),
    api_key: $("apiKey").value.trim(),
    base_url: $("baseUrl").value.trim(),
  };
}

function analysisPayload() {
  return {
    ...projectPayload(),
    ...llmPayload(),
    use_llm: $("useLlm").checked,
    style: $("reportStyle").value,
  };
}

function splitArgs(value) {
  const matches = String(value).match(/(?:[^\s"']+|"[^"]*"|'[^']*')+/g) || [];
  return matches.map((item) => {
    if ((item.startsWith('"') && item.endsWith('"')) || (item.startsWith("'") && item.endsWith("'"))) {
      return item.slice(1, -1);
    }
    return item;
  });
}

function diagnosePayload() {
  const mode = $("diagnoseMode").value;
  return {
    ...projectPayload(),
    mode,
    cmake_args: mode === "dry" ? [] : splitArgs($("cmakeArgs").value),
    build_args: splitArgs($("buildArgs").value),
    ctest_args: splitArgs($("ctestArgs").value),
    start_command: $("startCommand").value.trim(),
    benchmark_command: $("benchmarkCommand").value.trim(),
    stats_url: $("statsUrl").value.trim(),
    timeout: Number($("diagnoseTimeout").value || 180),
  };
}

function astPayload() {
  return {
    ...projectPayload(),
    compile_db: $("compileDb").value.trim(),
    ast_json: $("astJson").value.trim(),
    clang_bin: $("clangBin").value.trim() || "clang++",
    max_files: Number($("astMaxFiles").value || 3),
    timeout: Number($("astTimeout").value || 60),
  };
}

function renderJob(job, label) {
  $("jobPanel").hidden = false;
  $("jobLabel").textContent = label;
  $("jobMessage").textContent = job.message || "运行中";
  $("jobProgress").value = job.progress || 0;
  $("jobProgress").textContent = `${job.progress || 0}%`;
  $("jobLog").textContent = (job.logs || []).join("\n");
  $("jobLog").scrollTop = $("jobLog").scrollHeight;
  $("cancelBtn").disabled = !["queued", "running", "cancelling"].includes(job.status);
}

function applyJobResult(action, result) {
  const notice = result.llm_warning ? `> ${result.llm_warning}\n\n` : "";
  if (action === "analyze") {
    renderTalkScripts(result.talk_scripts);
    setReport(`${notice}${stripInterviewSection(result.markdown)}`);
    setJson({ llm_requested: result.llm_requested, used_llm: result.used_llm, llm_warning: result.llm_warning, analysis: result.analysis });
  } else if (action === "agent") {
    renderTalkScripts(result.talk_scripts);
    setReport(stripInterviewSection(result.markdown));
    renderTrace(result.trace);
    setJson({ analysis: result.analysis, trace: result.trace });
    showOutputView("traceView");
  } else if (action === "ask") {
    renderTalkScripts({});
    setReport(`${notice}${result.markdown}`);
    setJson({ llm_requested: result.llm_requested, used_llm: result.used_llm, llm_warning: result.llm_warning, report_path: result.report_path });
  } else if (action === "diagnose") {
    renderTalkScripts({});
    setReport(result.markdown);
    setJson(result.diagnostic);
  } else if (action === "ast") {
    renderTalkScripts({});
    setReport(result.markdown);
    setJson(result.ast);
  }
}

async function runJob(action, payload, label) {
  if (state.activeJobId) return;
  savePreferences();
  setBusy(true);
  setStatus(`${label}运行中`);
  $("jobPanel").hidden = false;
  try {
    const started = await postJson("/api/jobs", { action, payload });
    const jobId = started.job.id;
    state.activeJobId = jobId;
    renderJob(started.job, label);

    while (state.activeJobId === jobId) {
      await delay(500);
      const response = await requestJson(`/api/jobs/${jobId}`);
      const job = response.job;
      renderJob(job, label);
      if (job.status === "completed") {
        applyJobResult(action, job.result);
        setStatus(job.result.llm_warning ? `${label}完成，已使用离线结果` : `${label}完成`);
        await loadHistory();
        break;
      }
      if (job.status === "failed") {
        throw new Error(job.error || "任务执行失败");
      }
      if (job.status === "cancelled") {
        setStatus(`${label}已取消`);
        break;
      }
    }
  } catch (error) {
    setStatus(`${label}失败`);
    renderTalkScripts({});
    setReport(`# 执行失败\n\n- ${error.message}`);
  } finally {
    state.activeJobId = null;
    setBusy(false);
    $("cancelBtn").disabled = true;
  }
}

const historyLabels = {
  analyze: "项目分析",
  agent: "Agent Trace",
  ask: "面试问答",
  diagnose: "项目诊断",
  ast: "Clang AST",
};

function formatHistoryTime(value) {
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) return value;
  return new Intl.DateTimeFormat("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
  }).format(date);
}

function renderHistory(items) {
  if (!items.length) {
    $("historyList").innerHTML = '<p class="empty-copy">暂无历史记录</p>';
    return;
  }
  $("historyList").innerHTML = items
    .map(
      (item) => `
        <button class="history-item" type="button" data-history-id="${escapeHtml(item.id)}">
          <span class="history-title">${escapeHtml(historyLabels[item.action] || item.title || item.action)}</span>
          <span class="history-meta">${escapeHtml(item.project_name)} · ${escapeHtml(formatHistoryTime(item.created_at))}</span>
        </button>
      `,
    )
    .join("");
}

async function loadHistory() {
  try {
    const data = await requestJson("/api/history");
    renderHistory(data.items || []);
  } catch (error) {
    $("historyList").innerHTML = `<p class="empty-copy">${escapeHtml(error.message)}</p>`;
  }
}

async function openHistory(itemId) {
  if (state.activeJobId) return;
  try {
    setStatus("正在读取历史记录");
    const data = await requestJson(`/api/history/${encodeURIComponent(itemId)}`);
    const scripts = data.talk_scripts || {};
    setReport(Object.keys(scripts).length ? stripInterviewSection(data.markdown) : data.markdown);
    setJson(data.data);
    renderTalkScripts(scripts);
    if (data.item.action === "agent") {
      renderTrace(data.data.trace);
    }
    setStatus("历史记录已打开");
  } catch (error) {
    setStatus("历史记录读取失败");
    setReport(`# 读取失败\n\n- ${error.message}`);
  }
}

function clearOutput() {
  $("reportOutput").classList.add("empty-state");
  $("reportOutput").textContent = "等待分析结果";
  $("traceOutput").classList.add("empty-state");
  $("traceOutput").textContent = "等待 Agent Trace";
  renderTalkScripts({});
  $("jsonOutput").textContent = "{}";
  setStatus("就绪");
}

const preferenceIds = ["projectPath", "modelName", "baseUrl", "reportStyle", "useLlm"];

function savePreferences() {
  const values = {};
  preferenceIds.forEach((id) => {
    const element = $(id);
    values[id] = element.type === "checkbox" ? element.checked : element.value;
  });
  localStorage.setItem("projectagentcpp.preferences", JSON.stringify(values));
}

function loadPreferences() {
  try {
    const values = JSON.parse(localStorage.getItem("projectagentcpp.preferences") || "{}");
    preferenceIds.forEach((id) => {
      if (!(id in values)) return;
      const element = $(id);
      if (element.type === "checkbox") element.checked = Boolean(values[id]);
      else element.value = values[id];
    });
  } catch (_error) {
    localStorage.removeItem("projectagentcpp.preferences");
  }
}

function startAction(action, payloadFactory, label) {
  try {
    runJob(action, payloadFactory(), label);
  } catch (error) {
    setStatus(`${label}无法启动`);
    setReport(`# 参数错误\n\n- ${error.message}`);
  }
}

$("analyzeBtn").addEventListener("click", () => startAction("analyze", analysisPayload, "项目分析"));

$("agentBtn").addEventListener("click", () =>
  startAction(
    "agent",
    () => ({ ...projectPayload(), task: $("agentTask").value.trim() }),
    "Agent Trace",
  ),
);

$("askBtn").addEventListener("click", () =>
  startAction(
    "ask",
    () => ({
      ...projectPayload(),
      ...llmPayload(),
      use_llm: $("askUseLlm").checked,
      question: $("question").value.trim(),
    }),
    "面试回答",
  ),
);

$("diagnoseBtn").addEventListener("click", () => {
  try {
    const payload = diagnosePayload();
    if ((payload.start_command || payload.benchmark_command) && !window.confirm("诊断将执行你填写的本地命令，确认继续吗？")) {
      return;
    }
    startAction("diagnose", () => payload, "项目诊断");
  } catch (error) {
    setStatus("项目诊断无法启动");
    setReport(`# 参数错误\n\n- ${error.message}`);
  }
});

$("astBtn").addEventListener("click", () => startAction("ast", astPayload, "Clang AST"));

$("cancelBtn").addEventListener("click", async () => {
  if (!state.activeJobId) return;
  $("cancelBtn").disabled = true;
  try {
    await postJson(`/api/jobs/${state.activeJobId}/cancel`, {});
    setStatus("正在取消任务");
  } catch (error) {
    setStatus(`取消失败：${error.message}`);
  }
});

$("clearBtn").addEventListener("click", clearOutput);
$("refreshHistoryBtn").addEventListener("click", loadHistory);

$("talkDuration").addEventListener("change", () => {
  $("talkOutput").textContent = state.talkScripts[$("talkDuration").value] || "";
});

$("historyList").addEventListener("click", (event) => {
  const button = event.target.closest("[data-history-id]");
  if (button) openHistory(button.dataset.historyId);
});

document.querySelectorAll(".output-tab").forEach((tab) => {
  tab.addEventListener("click", () => showOutputView(tab.dataset.view));
});

document.querySelectorAll(".workflow-tab").forEach((tab) => {
  tab.addEventListener("click", () => showWorkflow(tab.dataset.workflow));
});

preferenceIds.forEach((id) => $(id).addEventListener("change", savePreferences));

loadPreferences();
loadHistory();
