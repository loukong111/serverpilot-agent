const state = {
  activeJobId: null,
  activeProposal: null,
  lastDiagnostic: null,
  busy: false,
  taskMode: "coding",
  taskDrafts: {
    analysis: "分析项目架构、核心模块、工程亮点和潜在风险",
    coding: "为项目补充一个小型功能，并添加对应单元测试",
    diagnosis: "构建项目并运行测试，定位失败原因",
  },
  confirmResolver: null,
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
  state.busy = busy;
  [
    "analyzeBtn",
    "agentBtn",
    "codingBtn",
    "diagnoseBtn",
    "astBtn",
    "askBtn",
    "clearBtn",
    "runAgentBtn",
    "newTaskBtn",
  ].forEach((id) => {
    $(id).disabled = busy;
  });
  updateProposalActions();
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

function showMainView(viewId) {
  document.querySelectorAll(".main-output-view").forEach((view) => {
    const active = view.id === viewId;
    view.classList.toggle("active", active);
    view.hidden = !active;
  });
  document.querySelectorAll(".main-output-tab").forEach((tab) => {
    const active = tab.dataset.view === viewId;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
  });
}

function showDetailView(viewId) {
  document.querySelectorAll(".detail-view").forEach((view) => {
    const active = view.id === viewId;
    view.classList.toggle("active", active);
    view.hidden = !active;
  });
  document.querySelectorAll(".detail-tab").forEach((tab) => {
    const active = tab.dataset.detailView === viewId;
    tab.classList.toggle("active", active);
    tab.setAttribute("aria-selected", String(active));
    tab.tabIndex = active ? 0 : -1;
  });
}

function openDetails(viewId = "logView") {
  showDetailView(viewId);
  $("detailsDrawer").hidden = false;
  $("drawerBackdrop").hidden = false;
  document.body.classList.add("drawer-open");
  $("closeDetailsBtn").focus();
}

function closeDetails() {
  $("detailsDrawer").hidden = true;
  $("drawerBackdrop").hidden = true;
  document.body.classList.remove("drawer-open");
}

function showOutputView(viewId) {
  if (["logView", "traceView", "jsonView"].includes(viewId)) {
    openDetails(viewId);
  } else {
    showMainView(viewId);
  }
}

const taskModeConfig = {
  analysis: {
    button: "开始项目分析",
    hint: "扫描工程结构和源码，生成架构、亮点、风险与面试讲法。",
    placeholder: "描述希望重点分析的模块或问题",
  },
  coding: {
    button: "生成修改方案",
    hint: "检索相关源码并生成候选 Diff，应用前需要确认。",
    placeholder: "描述需要实现或修复的代码任务",
  },
  diagnosis: {
    button: "开始项目诊断",
    hint: "使用项目设置中的参数执行构建与测试，并整理失败原因。",
    placeholder: "描述需要验证的构建或运行问题",
  },
};

function setTaskMode(mode, { preserveCurrent = true } = {}) {
  const config = taskModeConfig[mode];
  if (!config) return;
  const input = $("codingTask");
  if (preserveCurrent && state.taskMode && state.taskMode !== mode) {
    state.taskDrafts[state.taskMode] = input.value;
  }
  state.taskMode = mode;
  input.value = state.taskDrafts[mode] || "";
  input.placeholder = config.placeholder;
  $("modeHint").textContent = config.hint;
  $("runAgentBtn").textContent = config.button;
  document.querySelectorAll(".mode-button").forEach((button) => {
    const active = button.dataset.taskMode === mode;
    button.classList.toggle("active", active);
    button.setAttribute("aria-pressed", String(active));
  });
}

function openDialog(dialogId) {
  const dialog = $(dialogId);
  if (!dialog.open) dialog.showModal();
}

function closeDialog(dialogId) {
  const dialog = $(dialogId);
  if (dialog.open) dialog.close();
}

function finishConfirmation(accepted) {
  const resolver = state.confirmResolver;
  state.confirmResolver = null;
  closeDialog("confirmDialog");
  if (resolver) resolver(accepted);
}

function confirmAction(title, message, acceptLabel = "确认", danger = false) {
  if (state.confirmResolver) state.confirmResolver(false);
  $("confirmTitle").textContent = title;
  $("confirmMessage").textContent = message;
  $("confirmAcceptBtn").textContent = acceptLabel;
  $("confirmAcceptBtn").classList.toggle("danger", danger);
  $("confirmAcceptBtn").classList.toggle("primary", !danger);
  openDialog("confirmDialog");
  return new Promise((resolve) => {
    state.confirmResolver = resolve;
  });
}

function bindTabKeyboard(selector) {
  const tabs = [...document.querySelectorAll(selector)];
  tabs.forEach((tab, index) => {
    tab.addEventListener("keydown", (event) => {
      let targetIndex = null;
      if (["ArrowRight", "ArrowDown"].includes(event.key)) targetIndex = (index + 1) % tabs.length;
      else if (["ArrowLeft", "ArrowUp"].includes(event.key)) targetIndex = (index - 1 + tabs.length) % tabs.length;
      else if (event.key === "Home") targetIndex = 0;
      else if (event.key === "End") targetIndex = tabs.length - 1;
      if (targetIndex === null) return;
      event.preventDefault();
      tabs[targetIndex].focus();
      tabs[targetIndex].click();
    });
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

function renderDiff(patch) {
  const content = String(patch || "");
  const output = $("diffOutput");
  if (!content) {
    output.classList.add("empty-state");
    output.textContent = "等待 Coding Agent 生成候选补丁";
    return;
  }
  output.classList.remove("empty-state");
  output.innerHTML = content
    .split("\n")
    .map((line) => {
      let className = "diff-context";
      if (line.startsWith("diff --git") || line.startsWith("@@")) className = "diff-header";
      else if (line.startsWith("+") && !line.startsWith("+++")) className = "diff-add";
      else if (line.startsWith("-") && !line.startsWith("---")) className = "diff-remove";
      return `<span class="${className}">${escapeHtml(line)}</span>`;
    })
    .join("\n");
}

function updateProposalActions() {
  const status = state.activeProposal?.status;
  const diagnostic = state.lastDiagnostic?.diagnostic;
  const sameProject = diagnostic?.project_path === state.activeProposal?.project_path;
  const canRepair =
    sameProject &&
    diagnostic?.success === false &&
    diagnostic?.repairable === true &&
    Number(state.activeProposal?.round || 1) < 5;
  $("applyPatchBtn").disabled = state.busy || status !== "pending";
  $("rollbackPatchBtn").disabled = state.busy || status !== "applied";
  $("verifyPatchBtn").disabled = state.busy || status !== "applied";
  $("repairPatchBtn").disabled = state.busy || status !== "applied" || !canRepair;
}

function renderProposal(proposal) {
  state.activeProposal = proposal || null;
  $("proposalToolbar").hidden = !proposal;
  if (!proposal) {
    $("proposalStatus").textContent = "等待修改方案";
    renderDiff("");
    updateProposalActions();
    return;
  }
  const labels = {
    pending: "候选补丁待审核",
    applied: "补丁已应用，可构建测试或回滚",
    rolled_back: "补丁已回滚",
  };
  const round = Number(proposal.round || 1);
  let statusText = labels[proposal.status] || proposal.status;
  if (proposal.kind === "repair" && proposal.status === "pending") {
    statusText = `第 ${round} 轮修复待审核`;
  } else if (
    proposal.status === "applied" &&
    state.lastDiagnostic?.diagnostic?.project_path === proposal.project_path
  ) {
    const verification = state.lastDiagnostic.diagnostic.verification_status;
    if (verification === "passed") {
      statusText = `第 ${round} 轮修改已通过构建测试`;
    } else if (verification === "incomplete") {
      statusText = `第 ${round} 轮构建完成，但没有执行有效测试`;
    } else if (state.lastDiagnostic.diagnostic.repairable) {
      statusText = `第 ${round} 轮修改构建失败，可分析错误`;
    } else {
      statusText = `第 ${round} 轮运行诊断失败，请检查环境或参数`;
    }
  }
  $("proposalStatus").textContent = statusText;
  renderDiff(proposal.patch);
  updateProposalActions();
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
    task: $("codingTask").value.trim(),
    use_llm: $("useLlm").checked,
    style: $("reportStyle").value,
  };
}

function codingPayload() {
  const task = $("codingTask").value.trim();
  if (!task) throw new Error("请输入代码修改任务");
  return {
    ...projectPayload(),
    task,
    ...codingLlmPayload(),
  };
}

function codingLlmPayload() {
  return {
    model: $("codingModelName").value.trim() || $("modelName").value.trim(),
    api_key: $("codingApiKey").value.trim() || $("apiKey").value.trim(),
    base_url: $("codingBaseUrl").value.trim() || $("baseUrl").value.trim(),
  };
}

function repairPayload() {
  const proposal = state.activeProposal;
  const diagnostic = state.lastDiagnostic;
  if (!proposal || proposal.status !== "applied") throw new Error("当前没有可修复的已应用补丁");
  if (
    !diagnostic?.history_item?.id ||
    diagnostic.diagnostic?.success !== false ||
    diagnostic.diagnostic?.repairable !== true
  ) {
    throw new Error("当前没有失败的构建诊断");
  }
  return {
    ...projectPayload(),
    ...codingLlmPayload(),
    task: proposal.task,
    proposal_id: proposal.id,
    diagnostic_history_id: diagnostic.history_item.id,
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
    task: $("codingTask").value.trim(),
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
  } else if (action === "coding" || action === "coding_repair") {
    state.lastDiagnostic = null;
    renderTalkScripts({});
    renderProposal(result.proposal);
    setReport(result.markdown);
    renderTrace(result.trace);
    setJson({ proposal: result.proposal, matches: result.matches, trace: result.trace });
    showOutputView("diffView");
  } else if (action === "ask") {
    renderTalkScripts({});
    setReport(`${notice}${result.markdown}`);
    setJson({ llm_requested: result.llm_requested, used_llm: result.used_llm, llm_warning: result.llm_warning, report_path: result.report_path });
  } else if (action === "diagnose") {
    state.lastDiagnostic = {
      diagnostic: result.diagnostic,
      history_item: result.history_item,
    };
    renderTalkScripts({});
    setReport(result.markdown);
    setJson(result.diagnostic);
    if (state.activeProposal) renderProposal(state.activeProposal);
    const sameProject = result.diagnostic.project_path === state.activeProposal?.project_path;
    const canContinue = Number(state.activeProposal?.round || 1) < 5;
    if (
      result.diagnostic.success === false &&
      result.diagnostic.repairable === true &&
      state.activeProposal?.status === "applied" &&
      sameProject &&
      canContinue &&
      $("codingAutoLoop").checked
    ) {
      return { action: "coding_repair", payloadFactory: repairPayload, label: "生成修复方案" };
    }
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
  let followUp = null;
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
        followUp = applyJobResult(action, job.result);
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
  if (followUp) {
    window.setTimeout(
      () => startAction(followUp.action, followUp.payloadFactory, followUp.label),
      0,
    );
  }
}

const historyLabels = {
  analyze: "项目分析",
  agent: "Agent Trace",
  coding: "代码修改方案",
  coding_repair: "代码修复方案",
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
    } else if (["coding", "coding_repair"].includes(data.item.action)) {
      let proposal = data.data.proposal;
      try {
        const current = await requestJson(`/api/coding/proposals/${encodeURIComponent(proposal.id)}`);
        proposal = current.proposal;
      } catch (_error) {
        // The history snapshot remains readable if proposal storage was cleaned.
      }
      renderProposal(proposal);
      renderTrace(data.data.trace);
      showOutputView("diffView");
    } else if (data.item.action === "diagnose") {
      state.lastDiagnostic = { diagnostic: data.data, history_item: data.item };
      if (state.activeProposal) renderProposal(state.activeProposal);
    }
    setStatus("历史记录已打开");
  } catch (error) {
    setStatus("历史记录读取失败");
    setReport(`# 读取失败\n\n- ${error.message}`);
  }
}

function clearOutput() {
  $("reportOutput").classList.add("empty-state");
  $("reportOutput").innerHTML = "<h3>从一个任务开始</h3><p>选择模式并描述目标，Agent 会在这里整理计划、报告和验证结果。</p>";
  $("traceOutput").classList.add("empty-state");
  $("traceOutput").textContent = "等待 Agent Trace";
  $("jobLog").textContent = "暂无运行日志";
  $("jobPanel").hidden = true;
  state.lastJson = {};
  state.lastMarkdown = "";
  state.lastDiagnostic = null;
  renderProposal(null);
  renderTalkScripts({});
  $("jsonOutput").textContent = "{}";
  showMainView("reportView");
  closeDetails();
  setStatus("就绪");
}

const preferenceIds = [
  "projectPath",
  "modelName",
  "baseUrl",
  "reportStyle",
  "useLlm",
  "codingModelName",
  "codingBaseUrl",
  "codingAutoLoop",
];

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

function updateProjectDisplay() {
  const path = $("projectPath").value.trim().replace(/\/+$/, "");
  const name = path.split("/").filter(Boolean).at(-1) || "未选择项目";
  $("sidebarProjectName").textContent = name;
  $("sidebarProjectPath").textContent = path || "请选择项目路径";
  $("headerProjectName").textContent = name;
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

$("codingBtn").addEventListener("click", () =>
  startAction("coding", codingPayload, "代码修改方案"),
);

$("agentBtn").addEventListener("click", () =>
  {
    closeDialog("toolsDialog");
    startAction(
      "agent",
      () => ({ ...projectPayload(), task: $("agentTask").value.trim() }),
      "Agent Trace",
    );
  },
);

$("askBtn").addEventListener("click", () => {
  closeDialog("interviewDialog");
  startAction(
    "ask",
    () => ({
      ...projectPayload(),
      ...llmPayload(),
      use_llm: $("askUseLlm").checked,
      question: $("question").value.trim(),
    }),
    "面试回答",
  );
});

$("diagnoseBtn").addEventListener("click", async () => {
  try {
    const payload = diagnosePayload();
    if (payload.start_command || payload.benchmark_command) {
      const accepted = await confirmAction(
        "执行本地命令",
        "诊断会运行项目设置中填写的服务启动或压测命令。请确认这些命令来自可信项目。",
        "继续诊断",
      );
      if (!accepted) return;
    }
    startAction("diagnose", () => payload, "项目诊断");
  } catch (error) {
    setStatus("项目诊断无法启动");
    setReport(`# 参数错误\n\n- ${error.message}`);
  }
});

$("astBtn").addEventListener("click", () => {
  closeDialog("toolsDialog");
  startAction("ast", astPayload, "Clang AST");
});

$("applyPatchBtn").addEventListener("click", async () => {
  const proposal = state.activeProposal;
  if (!proposal || proposal.status !== "pending") return;
  const accepted = await confirmAction(
    "应用候选补丁",
    `这会直接修改项目中的 ${proposal.files.length} 个文件。应用后可以构建测试或回滚。`,
    "应用补丁",
  );
  if (!accepted) return;
  let autoTest = false;
  setBusy(true);
  setStatus("正在应用补丁");
  try {
    const result = await postJson("/api/coding/apply", { proposal_id: proposal.id });
    state.lastDiagnostic = null;
    renderProposal(result.proposal);
    setReport(result.markdown);
    setJson({ proposal: result.proposal });
    setStatus("补丁已应用");
    autoTest = $("codingAutoLoop").checked;
  } catch (error) {
    setStatus("补丁应用失败");
    setReport(`# 补丁应用失败\n\n- ${error.message}`);
  } finally {
    setBusy(false);
  }
  if (autoTest) {
    $("diagnoseMode").value = "build-test";
    setTaskMode("diagnosis");
    window.setTimeout(() => startAction("diagnose", diagnosePayload, "构建测试"), 0);
  }
});

$("rollbackPatchBtn").addEventListener("click", async () => {
  const proposal = state.activeProposal;
  if (!proposal || proposal.status !== "applied") return;
  const accepted = await confirmAction(
    "回滚项目修改",
    "项目文件将恢复到应用这份补丁之前的状态。",
    "确认回滚",
    true,
  );
  if (!accepted) return;
  setBusy(true);
  setStatus("正在回滚修改");
  try {
    const result = await postJson("/api/coding/rollback", { proposal_id: proposal.id });
    state.lastDiagnostic = null;
    renderProposal(result.proposal);
    setReport(result.markdown);
    setJson({ proposal: result.proposal });
    setStatus("修改已回滚");
  } catch (error) {
    setStatus("回滚失败");
    setReport(`# 回滚失败\n\n- ${error.message}`);
  } finally {
    setBusy(false);
  }
});

$("repairPatchBtn").addEventListener("click", () =>
  startAction("coding_repair", repairPayload, "生成修复方案"),
);

$("verifyPatchBtn").addEventListener("click", () => {
  state.lastDiagnostic = null;
  renderProposal(state.activeProposal);
  $("diagnoseMode").value = "build-test";
  setTaskMode("diagnosis");
  startAction("diagnose", diagnosePayload, "构建测试");
});

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

document.querySelectorAll(".main-output-tab").forEach((tab) => {
  tab.addEventListener("click", () => showMainView(tab.dataset.view));
});

document.querySelectorAll(".detail-tab").forEach((tab) => {
  tab.addEventListener("click", () => showDetailView(tab.dataset.detailView));
});

document.querySelectorAll(".mode-button").forEach((button) => {
  button.addEventListener("click", () => setTaskMode(button.dataset.taskMode));
});

$("runAgentBtn").addEventListener("click", () => {
  state.taskDrafts[state.taskMode] = $("codingTask").value;
  const actionButtons = {
    analysis: "analyzeBtn",
    coding: "codingBtn",
    diagnosis: "diagnoseBtn",
  };
  $(actionButtons[state.taskMode]).click();
});

$("newTaskBtn").addEventListener("click", () => {
  state.taskDrafts = {
    analysis: "分析项目架构、核心模块、工程亮点和潜在风险",
    coding: "为项目补充一个小型功能，并添加对应单元测试",
    diagnosis: "构建项目并运行测试，定位失败原因",
  };
  setTaskMode("coding", { preserveCurrent: false });
  clearOutput();
  $("codingTask").focus();
});

$("openProjectBtn").addEventListener("click", () => openDialog("projectDialog"));
$("openSettingsBtn").addEventListener("click", () => openDialog("settingsDialog"));
$("openSettingsTopBtn").addEventListener("click", () => openDialog("settingsDialog"));
$("openInterviewBtn").addEventListener("click", () => openDialog("interviewDialog"));
$("openToolsBtn").addEventListener("click", () => openDialog("toolsDialog"));
$("openDetailsBtn").addEventListener("click", () => openDetails("logView"));
$("openLogBtn").addEventListener("click", () => openDetails("logView"));
$("closeDetailsBtn").addEventListener("click", closeDetails);
$("drawerBackdrop").addEventListener("click", closeDetails);

document.querySelectorAll("[data-close-dialog]").forEach((button) => {
  button.addEventListener("click", () => {
    savePreferences();
    updateProjectDisplay();
    closeDialog(button.dataset.closeDialog);
  });
});

$("confirmCancelBtn").addEventListener("click", () => finishConfirmation(false));
$("confirmAcceptBtn").addEventListener("click", () => finishConfirmation(true));
$("confirmDialog").addEventListener("cancel", (event) => {
  event.preventDefault();
  finishConfirmation(false);
});

$("projectPath").addEventListener("input", updateProjectDisplay);
$("codingTask").addEventListener("input", () => {
  state.taskDrafts[state.taskMode] = $("codingTask").value;
});

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("detailsDrawer").hidden) closeDetails();
});

bindTabKeyboard(".main-output-tab");
bindTabKeyboard(".detail-tab");

preferenceIds.forEach((id) => $(id).addEventListener("change", savePreferences));

loadPreferences();
updateProjectDisplay();
setTaskMode("coding", { preserveCurrent: false });
loadHistory();
