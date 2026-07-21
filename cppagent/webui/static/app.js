const state = {
  activeJobId: null,
  activeProposal: null,
  lastDiagnostic: null,
  busy: false,
  taskMode: "coding",
  taskDrafts: {
    analysis: "分析项目架构、核心模块、工程亮点和潜在风险",
    coding: "",
    diagnosis: "构建项目并运行测试，定位失败原因",
  },
  serverLlmConfig: {
    model_configured: false,
    api_key_configured: false,
    openai_api_key_configured: false,
    openrouter_api_key_configured: false,
  },
  llmProvider: "ollama",
  providerDrafts: {
    ollama: { model: "qwen2.5-coder:1.5b", apiKey: "", baseUrl: "http://127.0.0.1:11434/v1" },
    openrouter: { model: "openrouter/free", apiKey: "", baseUrl: "https://openrouter.ai/api/v1" },
    custom: { model: "", apiKey: "", baseUrl: "https://api.openai.com/v1" },
  },
  ollamaStatus: null,
  confirmResolver: null,
  lastJson: {},
  lastMarkdown: "",
  lastDetailedMarkdown: "",
  talkScripts: {},
};

const $ = (id) => document.getElementById(id);
const delay = (milliseconds) => new Promise((resolve) => window.setTimeout(resolve, milliseconds));

const providerDefaults = {
  ollama: { model: "qwen2.5-coder:1.5b", apiKey: "", baseUrl: "http://127.0.0.1:11434/v1" },
  openrouter: { model: "openrouter/free", apiKey: "", baseUrl: "https://openrouter.ai/api/v1" },
  custom: { model: "", apiKey: "", baseUrl: "https://api.openai.com/v1" },
};
const ollamaCodingModel = "qwen2.5-coder:3b";
const preferencesVersion = 3;

function isDiagnosticOnlyTask(task) {
  const normalized = String(task || "").replace(/\s+/g, "");
  const hasDiagnosticIntent = /(测试|构建|编译|运行|启动|能不能正常|能否正常|是否正常|检查项目)/.test(normalized);
  const hasCodeChangeIntent = /(修改|新增|添加|实现|修复|改成|补充|删除|移除|重构|替换|优化代码)/.test(normalized);
  return hasDiagnosticIntent && !hasCodeChangeIntent;
}

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
    placeholder: "例如：为配置解析增加端口范围校验，并补充单元测试",
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
  const output = $("reportOutput");
  output.classList.remove("empty-state", "diagnostic-report");
  output.innerHTML = renderMarkdown(state.lastMarkdown);
  showOutputView("reportView");
}

function setDetailedReport(markdown) {
  state.lastDetailedMarkdown = String(markdown || "");
  const output = $("detailReportOutput");
  if (!state.lastDetailedMarkdown) {
    output.classList.add("empty-state");
    output.innerHTML = "<p>暂无详细报告</p>";
    return;
  }
  output.classList.remove("empty-state");
  output.innerHTML = renderMarkdown(state.lastDetailedMarkdown);
}

const diagnosticStepLabels = {
  configure: "CMake 配置",
  build: "项目构建",
  test: "CTest 测试",
  start_service: "服务启动",
  fetch_stats: "Stats 获取",
  benchmark: "性能压测",
  stop_service: "服务停止",
};

function formatDiagnosticDuration(milliseconds) {
  const value = Math.max(0, Number(milliseconds) || 0);
  if (value < 1000) return `${Math.round(value)} ms`;
  const seconds = value / 1000;
  if (seconds < 60) return `${seconds < 10 ? seconds.toFixed(1) : Math.round(seconds)} 秒`;
  const minutes = Math.floor(seconds / 60);
  const remainingSeconds = Math.round(seconds % 60);
  return remainingSeconds ? `${minutes} 分 ${remainingSeconds} 秒` : `${minutes} 分钟`;
}

function diagnosticTestStats(diagnostic) {
  const testStep = (diagnostic?.steps || []).find((step) => step.name === "test");
  const output = `${testStep?.stdout || ""}\n${testStep?.stderr || ""}`;
  const match = output.match(/\d+% tests passed,\s*(\d+) tests failed out of (\d+)/i);
  if (!match) return null;
  const failed = Number(match[1]);
  const total = Number(match[2]);
  return { failed, total, passed: Math.max(0, total - failed) };
}

function diagnosticFailureExcerpt(step) {
  if (!step) return "";
  const lines = `${step.stderr || ""}\n${step.stdout || ""}`
    .split("\n")
    .map((line) => line.trim())
    .filter(Boolean);
  const relevant = lines.filter((line) =>
    /(error|failed|failure|not found|cannot|unable|did not|timeout|timed out|undefined reference|失败|错误)/i.test(line),
  );
  return [...new Set((relevant.length ? relevant : lines.slice(-3)).slice(0, 4))].join("\n");
}

function renderDiagnosticSummary(diagnostic) {
  const steps = diagnostic?.steps || [];
  const failedStep = steps.find((step) => !step.success && !step.skipped);
  const verification = diagnostic?.verification_status || (failedStep ? "failed" : "incomplete");
  const testStats = diagnosticTestStats(diagnostic);
  const projectPath = String(diagnostic?.project_path || "未知项目");
  const projectName = projectPath.split("/").filter(Boolean).at(-1) || projectPath;
  const totalDuration = steps.reduce(
    (total, step) => total + (step.skipped ? 0 : Math.max(0, Number(step.duration_ms) || 0)),
    0,
  );

  let tone = "warn";
  let badge = "结果不完整";
  let title = "诊断完成，验证不完整";
  let description = "本次没有执行完整的构建与测试流程。";
  if (verification === "passed") {
    tone = "ok";
    badge = "验证通过";
    title = testStats ? "构建与测试通过" : "项目诊断通过";
    description = testStats
      ? `项目已成功完成配置和构建，${testStats.passed}/${testStats.total} 项测试全部通过。`
      : "本次执行的诊断步骤均已通过。";
  } else if (verification === "failed") {
    tone = "fail";
    badge = "发现问题";
    title = `${diagnosticStepLabels[failedStep?.name] || "项目诊断"}未通过`;
    description = `诊断在“${diagnosticStepLabels[failedStep?.name] || failedStep?.name || "未知"}”步骤发现失败。`;
  } else if (diagnostic?.tests_found === false) {
    description = "项目构建已完成，但 CTest 没有发现可执行测试。";
  }

  const testFact = testStats
    ? `${testStats.passed}/${testStats.total} 通过`
    : diagnostic?.tests_found === false
      ? "未发现测试"
      : "未执行";
  const rows = steps
    .map((step) => {
      const status = step.skipped
        ? { tone: "skip", label: "未执行", detail: "本次未运行" }
        : step.success
          ? { tone: "ok", label: "通过", detail: "执行成功" }
          : { tone: "fail", label: "失败", detail: `退出码 ${step.exit_code ?? "未知"}` };
      if (step.name === "test" && testStats) {
        status.detail = `${testStats.passed}/${testStats.total} 项测试通过`;
      }
      return `
        <div class="diagnostic-step-row">
          <span class="status-badge ${status.tone}">${status.label}</span>
          <div>
            <strong>${escapeHtml(diagnosticStepLabels[step.name] || step.name)}</strong>
            <p>${escapeHtml(status.detail)}</p>
          </div>
          <span class="diagnostic-duration">${step.skipped ? "未计时" : escapeHtml(formatDiagnosticDuration(step.duration_ms))}</span>
        </div>
      `;
    })
    .join("");

  let attention = "";
  if (failedStep) {
    const excerpt = diagnosticFailureExcerpt(failedStep);
    const suggestion = (diagnostic?.suggestions || []).find(
      (item) => !/(压测|benchmark|stats|可观测性)/i.test(item),
    );
    attention = `
      <section class="diagnostic-attention">
        <h2>需要处理</h2>
        ${excerpt ? `<pre><code>${escapeHtml(excerpt)}</code></pre>` : ""}
        ${suggestion ? `<p>${escapeHtml(suggestion)}</p>` : ""}
      </section>
    `;
  }

  return `
    <section class="diagnostic-summary">
      <header class="diagnostic-overview ${tone}">
        <div>
          <p class="eyebrow">Verification</p>
          <h1>${escapeHtml(title)}</h1>
          <p>${escapeHtml(description)}</p>
        </div>
        <span class="status-badge ${tone}">${escapeHtml(badge)}</span>
      </header>
      <dl class="diagnostic-facts">
        <div><dt>项目</dt><dd>${escapeHtml(projectName)}</dd></div>
        <div><dt>总耗时</dt><dd>${escapeHtml(formatDiagnosticDuration(totalDuration))}</dd></div>
        <div><dt>测试</dt><dd>${escapeHtml(testFact)}</dd></div>
      </dl>
      <section class="diagnostic-checks">
        <h2>检查结果</h2>
        <div class="diagnostic-step-list">${rows}</div>
      </section>
      ${attention}
    </section>
  `;
}

function setDiagnosticSummary(diagnostic) {
  state.lastMarkdown = "";
  const output = $("reportOutput");
  output.classList.remove("empty-state");
  output.classList.add("diagnostic-report");
  output.innerHTML = renderDiagnosticSummary(diagnostic);
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
    provider: $("llmProvider").value,
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
  const provider = $("llmProvider").value;
  const model = $("modelName").value.trim();
  return {
    provider,
    model: provider === "ollama" ? $("codingModelName").value.trim() || model : model,
    api_key: $("codingApiKey").value.trim() || $("apiKey").value.trim(),
    base_url: $("codingBaseUrl").value.trim() || $("baseUrl").value.trim(),
  };
}

function saveCurrentProviderDraft() {
  state.providerDrafts[state.llmProvider] = {
    model: $("modelName").value.trim(),
    apiKey: $("apiKey").value.trim(),
    baseUrl: $("baseUrl").value.trim(),
  };
}

function renderProviderStatus() {
  const provider = state.llmProvider;
  const status = $("providerStatus");
  const title = $("providerStatusTitle");
  const detail = $("providerStatusDetail");
  const help = $("providerHelpLink");
  const check = $("checkProviderBtn");
  status.className = "provider-status";
  check.hidden = provider !== "ollama";
  help.hidden = provider === "custom";

  if (provider === "openrouter") {
    status.classList.add("ready");
    title.textContent = "OpenRouter 免费路由";
    detail.textContent = "使用个人免费 Key，模型由免费路由自动选择";
    help.href = "https://openrouter.ai/settings/keys";
    help.textContent = "获取 Key";
    return;
  }
  if (provider === "custom") {
    status.classList.add("ready");
    title.textContent = "OpenAI-compatible API";
    detail.textContent = "使用自定义模型、Key 和接口地址";
    return;
  }

  help.href = "https://ollama.com/download";
  help.textContent = "获取 Ollama";
  if (!state.ollamaStatus) {
    status.classList.add("checking");
    title.textContent = "正在检测 Ollama";
    detail.textContent = "检查本地免费模型服务";
    return;
  }
  if (!state.ollamaStatus.available) {
    status.classList.add("error");
    title.textContent = "未连接 Ollama";
    detail.textContent = "本地 11434 端口没有可用服务";
    return;
  }
  const model = $("modelName").value.trim();
  if (!state.ollamaStatus.models.includes(model)) {
    status.classList.add("warning");
    title.textContent = "当前模型尚未安装";
    detail.textContent = model || "请选择一个本地模型";
    help.href = "https://ollama.com/library/qwen2.5-coder";
    help.textContent = "查看模型";
    return;
  }
  status.classList.add("ready");
  title.textContent = "本地免费模型已就绪";
  detail.textContent = model;
}

async function checkOllamaStatus() {
  state.ollamaStatus = null;
  renderProviderStatus();
  try {
    const data = await requestJson("/api/providers/ollama");
    state.ollamaStatus = data.ollama;
  } catch (_error) {
    state.ollamaStatus = { available: false, models: [] };
  }
  const options = (state.ollamaStatus.models || []).map((model) => {
    const option = document.createElement("option");
    option.value = model;
    return option;
  });
  $("ollamaModels").replaceChildren(...options);
  renderProviderStatus();
  return state.ollamaStatus;
}

function setLlmProvider(provider, { initialize = false } = {}) {
  if (!providerDefaults[provider]) return;
  if (!initialize) saveCurrentProviderDraft();
  state.llmProvider = provider;
  $("llmProvider").value = provider;
  const draft = initialize
    ? {
        model: $("modelName").value.trim(),
        apiKey: $("apiKey").value.trim(),
        baseUrl: $("baseUrl").value.trim(),
      }
    : state.providerDrafts[provider] || providerDefaults[provider];
  state.providerDrafts[provider] = { ...providerDefaults[provider], ...draft };
  $("modelName").value = draft.model || providerDefaults[provider].model;
  $("apiKey").value = draft.apiKey || "";
  $("baseUrl").value =
    provider === "custom"
      ? draft.baseUrl || providerDefaults.custom.baseUrl
      : providerDefaults[provider].baseUrl;
  $("apiKeyField").hidden = provider === "ollama";
  $("baseUrlField").hidden = provider !== "custom";
  $("codingModelField").hidden = provider !== "ollama";
  if (provider === "ollama" && !$("codingModelName").value.trim()) {
    $("codingModelName").value = ollamaCodingModel;
  }
  document.querySelectorAll(".provider-button").forEach((button) => {
    const active = button.dataset.provider === provider;
    button.classList.toggle("active", active);
    button.setAttribute("aria-checked", String(active));
  });
  $("settingsNotice").hidden = true;
  renderProviderStatus();
  if (provider === "ollama") checkOllamaStatus();
}

function codingConfigurationIssues() {
  const payload = codingLlmPayload();
  const issues = [];
  if (!payload.model && !state.serverLlmConfig.model_configured) issues.push("模型名称");
  const keyConfigured =
    payload.provider === "ollama" ||
    (payload.provider === "openrouter" && state.serverLlmConfig.openrouter_api_key_configured) ||
    (payload.provider === "custom" && state.serverLlmConfig.openai_api_key_configured);
  if (!payload.api_key && !keyConfigured) issues.push("API Key");
  return issues;
}

function codingConfigurationMessage(issues = codingConfigurationIssues()) {
  if (!issues.length) return "当前模型来源尚未准备好，请检查连接状态。";
  const missing = issues.length === 2 ? "模型名称和 API Key" : issues[0];
  return `修改代码需要 LLM，请先填写${missing}。保存后可以继续当前任务。`;
}

function showCodingConfiguration(message = "", preferredFocus = "") {
  const issues = codingConfigurationIssues();
  const notice = $("settingsNotice");
  notice.textContent = message || codingConfigurationMessage(issues);
  notice.hidden = false;
  setStatus("请先完成模型设置");
  openDialog("settingsDialog");
  const focusId =
    preferredFocus || (issues.includes("模型名称") ? "modelName" : "apiKey");
  window.setTimeout(() => $(focusId).focus(), 0);
}

async function ensureCodingConfiguration() {
  const issues = codingConfigurationIssues();
  if (issues.length) {
    showCodingConfiguration();
    return false;
  }
  if (state.llmProvider === "ollama") {
    const ollama = await checkOllamaStatus();
    if (!ollama.available) {
      showCodingConfiguration(
        "未检测到本地 Ollama 服务。安装并启动 Ollama 后即可免费生成修改方案。",
        "checkProviderBtn",
      );
      return false;
    }
    const model = $("modelName").value.trim();
    if (!ollama.models.includes(model)) {
      showCodingConfiguration(
        `Ollama 已连接，但本地尚未安装模型 ${model}。`,
        "modelName",
      );
      return false;
    }
  }
  $("settingsNotice").hidden = true;
  return true;
}

async function loadServerConfiguration() {
  try {
    const data = await requestJson("/api/config");
    state.serverLlmConfig = data.llm || state.serverLlmConfig;
  } catch (_error) {
    // The form values remain the source of truth if the status endpoint is unavailable.
  }
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

function formatElapsed(createdAt) {
  const startedAt = Date.parse(createdAt || "");
  if (!Number.isFinite(startedAt)) return "";
  const totalSeconds = Math.max(0, Math.floor((Date.now() - startedAt) / 1000));
  if (totalSeconds < 60) return `${totalSeconds} 秒`;
  return `${Math.floor(totalSeconds / 60)} 分 ${totalSeconds % 60} 秒`;
}

function renderJob(job, label) {
  $("jobPanel").hidden = false;
  $("jobLabel").textContent = label;
  let message = job.message || "运行中";
  const modelGenerating =
    job.status === "running" && message.includes("Coding Agent 正在生成");
  if (modelGenerating) {
    const elapsed = formatElapsed(job.created_at);
    if (elapsed) message = `${message} · 已等待 ${elapsed}`;
  }
  $("jobMessage").textContent = message;
  if (modelGenerating) {
    $("jobProgress").removeAttribute("value");
    $("jobProgress").textContent = "模型生成中";
  } else {
    $("jobProgress").value = job.progress || 0;
    $("jobProgress").textContent = `${job.progress || 0}%`;
  }
  $("jobLog").textContent = (job.logs || []).join("\n");
  $("jobLog").scrollTop = $("jobLog").scrollHeight;
  $("cancelBtn").disabled = !["queued", "running", "cancelling"].includes(job.status);
}

function applyJobResult(action, result) {
  const notice = result.llm_warning ? `> ${result.llm_warning}\n\n` : "";
  setDetailedReport(result.markdown || "");
  if (action !== "diagnose") state.lastDiagnostic = null;
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
    setDiagnosticSummary(result.diagnostic);
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
    const codingConfigurationError =
      ["coding", "coding_repair"].includes(action) &&
      /(API Key|模型|Missing API key|Missing model)/i.test(error.message);
    if (codingConfigurationError) {
      $("jobPanel").hidden = true;
      showCodingConfiguration(error.message);
    } else {
      setStatus(`${label}失败`);
      renderTalkScripts({});
      setReport(`# 执行失败\n\n- ${error.message}`);
    }
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
    state.lastDiagnostic = null;
    setDetailedReport(data.markdown);
    if (data.item.action === "diagnose") setDiagnosticSummary(data.data);
    else setReport(Object.keys(scripts).length ? stripInterviewSection(data.markdown) : data.markdown);
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
  $("reportOutput").classList.remove("diagnostic-report");
  $("reportOutput").innerHTML = "<h3>从一个任务开始</h3><p>选择模式并描述目标，Agent 会在这里整理计划、报告和验证结果。</p>";
  $("traceOutput").classList.add("empty-state");
  $("traceOutput").textContent = "等待 Agent Trace";
  $("jobLog").textContent = "暂无运行日志";
  $("jobPanel").hidden = true;
  state.lastJson = {};
  state.lastMarkdown = "";
  state.lastDetailedMarkdown = "";
  state.lastDiagnostic = null;
  setDetailedReport("");
  renderProposal(null);
  renderTalkScripts({});
  $("jsonOutput").textContent = "{}";
  showMainView("reportView");
  closeDetails();
  setStatus("就绪");
}

const preferenceIds = [
  "projectPath",
  "llmProvider",
  "modelName",
  "baseUrl",
  "reportStyle",
  "useLlm",
  "codingModelName",
  "codingBaseUrl",
  "codingAutoLoop",
];

function savePreferences() {
  const values = { version: preferencesVersion };
  preferenceIds.forEach((id) => {
    const element = $(id);
    values[id] = element.type === "checkbox" ? element.checked : element.value;
  });
  localStorage.setItem("projectagentcpp.preferences", JSON.stringify(values));
}

function loadPreferences() {
  try {
    const values = JSON.parse(localStorage.getItem("projectagentcpp.preferences") || "{}");
    if (!("llmProvider" in values)) {
      values.llmProvider = String(values.modelName || "").trim() ? "custom" : "ollama";
    }
    const storedVersion = Number(values.version || 1);
    if (storedVersion < 2 && values.llmProvider === "ollama") {
      if (values.modelName === "qwen2.5-coder:3b") values.modelName = providerDefaults.ollama.model;
    }
    if (storedVersion < 3 && values.llmProvider === "ollama") {
      values.codingModelName = ollamaCodingModel;
    }
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

async function startAction(action, payloadFactory, label) {
  if (
    ["coding", "coding_repair"].includes(action) &&
    !(await ensureCodingConfiguration())
  ) return;
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
  const task = $("codingTask").value.trim();
  state.taskDrafts[state.taskMode] = task;
  if (state.taskMode === "coding" && isDiagnosticOnlyTask(task)) {
    state.taskDrafts.diagnosis = task;
    setTaskMode("diagnosis");
    $("diagnoseMode").value = "build-test";
    setStatus("已按任务意图切换到项目诊断");
    $("diagnoseBtn").click();
    return;
  }
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
    coding: "",
    diagnosis: "构建项目并运行测试，定位失败原因",
  };
  setTaskMode("coding", { preserveCurrent: false });
  clearOutput();
  $("codingTask").focus();
});

$("openProjectBtn").addEventListener("click", () => openDialog("projectDialog"));
$("openSettingsBtn").addEventListener("click", () => {
  renderProviderStatus();
  openDialog("settingsDialog");
});
$("openSettingsTopBtn").addEventListener("click", () => {
  renderProviderStatus();
  openDialog("settingsDialog");
});
$("openInterviewBtn").addEventListener("click", () => openDialog("interviewDialog"));
$("openToolsBtn").addEventListener("click", () => openDialog("toolsDialog"));
$("openDetailsBtn").addEventListener("click", () =>
  openDetails(state.lastDiagnostic ? "detailReportView" : "logView"),
);
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

["modelName", "apiKey"].forEach((id) => {
  $(id).addEventListener("input", () => {
    saveCurrentProviderDraft();
    if (id === "modelName") renderProviderStatus();
    const issues = codingConfigurationIssues();
    if (!issues.length) {
      $("settingsNotice").hidden = true;
    } else if (!$("settingsNotice").hidden) {
      $("settingsNotice").textContent = codingConfigurationMessage(issues);
    }
  });
});

document.querySelectorAll(".provider-button").forEach((button) => {
  button.addEventListener("click", () => {
    setLlmProvider(button.dataset.provider);
    savePreferences();
  });
});

$("checkProviderBtn").addEventListener("click", checkOllamaStatus);

document.addEventListener("keydown", (event) => {
  if (event.key === "Escape" && !$("detailsDrawer").hidden) closeDetails();
});

bindTabKeyboard(".main-output-tab");
bindTabKeyboard(".detail-tab");

preferenceIds.forEach((id) => $(id).addEventListener("change", savePreferences));

loadPreferences();
setLlmProvider($("llmProvider").value, { initialize: true });
updateProjectDisplay();
setTaskMode("coding", { preserveCurrent: false });
loadServerConfiguration();
loadHistory();
