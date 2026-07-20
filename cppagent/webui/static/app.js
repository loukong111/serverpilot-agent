const state = {
  lastJson: {},
  lastMarkdown: "",
  talkScripts: {},
};

const $ = (id) => document.getElementById(id);

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
    .replaceAll('"', "&quot;");
}

function inlineMarkdown(value) {
  return escapeHtml(value).replace(/`([^`]+)`/g, "<code>$1</code>");
}

function renderMarkdown(markdown) {
  const lines = markdown.split("\n");
  const html = [];
  let inList = false;
  let inCode = false;
  let codeLines = [];

  function closeList() {
    if (inList) {
      html.push("</ul>");
      inList = false;
    }
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
      if (!inList) {
        html.push("<ul>");
        inList = true;
      }
      html.push(`<li>${inlineMarkdown(line.slice(2))}</li>`);
    } else if (line.trim() === "") {
      closeList();
    } else {
      closeList();
      html.push(`<p>${inlineMarkdown(line)}</p>`);
    }
  }
  closeList();
  return html.join("\n");
}

function showView(viewId) {
  document.querySelectorAll(".view").forEach((view) => view.classList.remove("active"));
  document.querySelectorAll(".tab").forEach((tab) => tab.classList.remove("active"));
  $(viewId).classList.add("active");
  document.querySelector(`[data-view="${viewId}"]`).classList.add("active");
}

function setReport(markdown) {
  state.lastMarkdown = markdown;
  $("reportOutput").classList.remove("empty-state");
  $("reportOutput").innerHTML = renderMarkdown(markdown);
  showView("reportView");
}

function stripInterviewSection(markdown) {
  const start = markdown.indexOf("\n## 面试讲法\n");
  if (start < 0) {
    return markdown;
  }
  const next = markdown.indexOf("\n## 推荐追问\n", start + 1);
  if (next < 0) {
    return markdown.slice(0, start).trimEnd();
  }
  return `${markdown.slice(0, start).trimEnd()}\n\n${markdown.slice(next).trimStart()}`;
}

function setJson(data) {
  state.lastJson = data;
  $("jsonOutput").textContent = JSON.stringify(data, null, 2);
}

function renderTalkScripts(scripts) {
  state.talkScripts = scripts || {};
  const panel = $("talkPanel");
  const duration = $("talkDuration");
  const output = $("talkOutput");
  if (!Object.keys(state.talkScripts).length) {
    panel.classList.add("hidden");
    output.textContent = "";
    return;
  }
  panel.classList.remove("hidden");
  duration.value = state.talkScripts["30s"] ? "30s" : Object.keys(state.talkScripts)[0];
  output.textContent = state.talkScripts[duration.value] || "";
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
      const ok = step.success ? "ok" : "fail";
      const label = step.success ? "OK" : "FAIL";
      return `
        <section class="trace-step">
          <span class="pill ${ok}">${label}</span>
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

async function postJson(url, payload) {
  const response = await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  const data = await response.json();
  if (!response.ok || !data.ok) {
    throw new Error(data.error || data.stderr || "Request failed");
  }
  return data;
}

function projectPayload() {
  return { project_path: $("projectPath").value.trim() };
}

function llmPayload() {
  const model = $("modelName").value.trim();
  return {
    model,
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
  return value
    .split(/\s+/)
    .map((item) => item.trim())
    .filter(Boolean);
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

async function runAction(label, task) {
  setBusy(true);
  setStatus(`${label} running`);
  try {
    await task();
    setStatus(`${label} done`);
  } catch (error) {
    setStatus(`${label} failed`);
    setReport(`# Error\n\n- ${error.message}`);
  } finally {
    setBusy(false);
  }
}

$("analyzeBtn").addEventListener("click", () => {
  runAction("Analyze", async () => {
    if ($("useLlm").checked) {
      setStatus("Analyze running with LLM");
    }
    const data = await postJson("/api/analyze", analysisPayload());
    renderTalkScripts(data.talk_scripts);
    setReport(stripInterviewSection(data.markdown));
    setJson({ used_llm: data.used_llm, analysis: data.analysis });
  });
});

$("agentBtn").addEventListener("click", () => {
  runAction("Trace", async () => {
    const data = await postJson("/api/agent", projectPayload());
    setJson({ analysis: data.analysis, trace: data.trace });
    renderTalkScripts(data.talk_scripts);
    renderTrace(data.trace);
    showView("traceView");
  });
});

$("askBtn").addEventListener("click", () => {
  runAction("Ask", async () => {
    const data = await postJson("/api/ask", {
      ...projectPayload(),
      ...llmPayload(),
      use_llm: $("askUseLlm").checked,
      question: $("question").value.trim(),
    });
    setReport(data.markdown);
    renderTalkScripts({});
    setJson({ report_path: data.report_path });
  });
});

$("diagnoseBtn").addEventListener("click", () => {
  runAction("Diagnose", async () => {
    const data = await postJson("/api/diagnose", diagnosePayload());
    setReport(data.markdown);
    renderTalkScripts({});
    setJson(data.diagnostic);
  });
});

$("astBtn").addEventListener("click", () => {
  runAction("AST", async () => {
    const data = await postJson("/api/ast", astPayload());
    setReport(data.markdown);
    renderTalkScripts({});
    setJson(data.ast);
  });
});

$("clearBtn").addEventListener("click", () => {
  $("reportOutput").classList.add("empty-state");
  $("reportOutput").textContent = "等待分析结果";
  $("traceOutput").classList.add("empty-state");
  $("traceOutput").textContent = "等待 Agent Trace";
  renderTalkScripts({});
  $("jsonOutput").textContent = "{}";
  setStatus("Ready");
});

$("talkDuration").addEventListener("change", () => {
  $("talkOutput").textContent = state.talkScripts[$("talkDuration").value] || "";
});

document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => showView(tab.dataset.view));
});
