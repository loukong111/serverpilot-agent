from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import unquote, urlsplit

ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "static"
REPORT_DIR = ROOT / "reports" / "web"
HISTORY_DIR = REPORT_DIR / "history"
MAX_REQUEST_BYTES = 1024 * 1024

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from agent.project_agent.cli import (  # noqa: E402
    build_interview_scripts,
    default_analyzer_path,
    generate_offline_interview_answer,
    generate_report,
    run_analyzer,
)
from agent.project_agent.clang_ast import (  # noqa: E402
    analyze_ast,
    ast_result_to_json,
    generate_ast_report,
)
from agent.project_agent.diagnostics import (  # noqa: E402
    generate_diagnostic_report,
    result_to_json,
    run_diagnostics,
)
from agent.project_agent.llm_client import (  # noqa: E402
    LLMClient,
    LLMConfig,
    LLMConfigurationError,
    LLMRequestError,
)
from agent.project_agent.prompts import build_interview_messages, build_report_messages  # noqa: E402


ProgressCallback = Callable[[int, str], None]
CancelCallback = Callable[[], bool]
ActionHandler = Callable[[dict[str, Any], ProgressCallback, CancelCallback], dict[str, Any]]

STYLE_PROMPTS = {
    "interview": "这次报告请偏面试表达，强调候选人如何讲清楚项目价值、技术亮点和可追问点。",
    "resume": "这次报告请偏简历表达，强调可量化的项目亮点、工程能力和简历 bullet 写法。",
    "deep": "这次报告请偏深挖问题，强调架构边界、潜在风险、测试缺口和下一步改进路线。",
}

ANALYSIS_CACHE: dict[str, tuple[tuple[int, int], dict[str, Any]]] = {}
CACHE_LOCK = threading.Lock()


class JobCancelled(RuntimeError):
    pass


def noop_progress(_value: int, _message: str) -> None:
    return


def never_cancel() -> bool:
    return False


def check_cancel(should_cancel: CancelCallback) -> None:
    if should_cancel():
        raise JobCancelled("任务已取消")


def ensure_analyzer() -> Path:
    analyzer = default_analyzer_path()
    if analyzer.exists():
        return analyzer
    raise RuntimeError("cpp_analyzer 尚未构建，请先运行 scripts/start_webui.sh。")


def json_response(handler: BaseHTTPRequestHandler, payload: dict[str, Any], status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0:
        return {}
    if length > MAX_REQUEST_BYTES:
        raise ValueError("Request body is too large.")
    body = handler.rfile.read(length).decode("utf-8")
    value = json.loads(body)
    if not isinstance(value, dict):
        raise ValueError("JSON body must be an object.")
    return value


def safe_project_path(value: Any) -> Path:
    project_path = Path(str(value or "")).expanduser().resolve()
    if not project_path.exists() or not project_path.is_dir():
        raise ValueError(f"项目路径不是有效目录：{project_path}")
    return project_path


def output_paths(prefix: str) -> tuple[Path, Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    return REPORT_DIR / f"{prefix}.md", REPORT_DIR / f"{prefix}.json"


def project_signature(project_path: Path) -> tuple[int, int]:
    latest_mtime = 0
    file_count = 0
    ignored = {".git", ".idea", ".vscode", "build", "cmake-build-debug", "cmake-build-release", "__pycache__"}
    relevant_suffixes = {".c", ".cc", ".cpp", ".cxx", ".h", ".hh", ".hpp", ".cmake", ".md", ".txt"}
    for current, directories, files in os.walk(project_path):
        directories[:] = [name for name in directories if name not in ignored]
        for name in files:
            path = Path(current) / name
            if name != "CMakeLists.txt" and path.suffix.lower() not in relevant_suffixes:
                continue
            try:
                latest_mtime = max(latest_mtime, path.stat().st_mtime_ns)
                file_count += 1
            except OSError:
                continue
    return file_count, latest_mtime


def get_analysis(project_path: Path, progress: ProgressCallback, should_cancel: CancelCallback) -> dict[str, Any]:
    progress(12, "正在检查项目文件")
    signature = project_signature(project_path)
    cache_key = str(project_path)
    with CACHE_LOCK:
        cached = ANALYSIS_CACHE.get(cache_key)
    if cached and cached[0] == signature:
        progress(34, "已复用最近一次项目分析")
        return cached[1]

    check_cancel(should_cancel)
    progress(22, "C++ Analyzer 正在扫描项目")
    data = run_analyzer(ensure_analyzer(), project_path)
    with CACHE_LOCK:
        ANALYSIS_CACHE[cache_key] = (signature, data)
    progress(48, "项目事实提取完成")
    return data


def history_id() -> str:
    timestamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    return f"{timestamp}-{uuid.uuid4().hex[:8]}"


def save_history(
    action: str,
    project_path: Path,
    title: str,
    markdown: str,
    data: Any,
    used_llm: bool = False,
) -> dict[str, Any]:
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    item_id = history_id()
    markdown_path = HISTORY_DIR / f"{item_id}.md"
    json_path = HISTORY_DIR / f"{item_id}.json"
    metadata_path = HISTORY_DIR / f"{item_id}.meta.json"
    created_at = datetime.now().astimezone().isoformat(timespec="seconds")
    metadata = {
        "id": item_id,
        "action": action,
        "title": title,
        "project_name": project_path.name,
        "project_path": str(project_path),
        "created_at": created_at,
        "used_llm": used_llm,
        "report_path": str(markdown_path),
        "json_path": str(json_path),
    }
    markdown_path.write_text(markdown, encoding="utf-8")
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    return metadata


def list_history(limit: int = 40) -> list[dict[str, Any]]:
    if not HISTORY_DIR.exists():
        return []
    items: list[dict[str, Any]] = []
    for path in sorted(HISTORY_DIR.glob("*.meta.json"), reverse=True):
        try:
            items.append(json.loads(path.read_text(encoding="utf-8")))
        except (OSError, json.JSONDecodeError):
            continue
        if len(items) >= limit:
            break
    return items


def load_history(item_id: str) -> dict[str, Any]:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", item_id):
        raise ValueError("Invalid history id.")
    metadata_path = HISTORY_DIR / f"{item_id}.meta.json"
    markdown_path = HISTORY_DIR / f"{item_id}.md"
    json_path = HISTORY_DIR / f"{item_id}.json"
    if not metadata_path.exists() or not markdown_path.exists() or not json_path.exists():
        raise FileNotFoundError("History item not found.")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    data = json.loads(json_path.read_text(encoding="utf-8"))
    analysis = data.get("analysis") if metadata.get("action") == "agent" else data
    talk_scripts = build_interview_scripts(analysis) if metadata.get("action") in {"analyze", "agent"} and isinstance(analysis, dict) else {}
    return {
        "ok": True,
        "item": metadata,
        "markdown": markdown_path.read_text(encoding="utf-8"),
        "data": data,
        "talk_scripts": talk_scripts,
    }


def run_agent_trace(project_path: Path, task: str) -> tuple[dict[str, Any], dict[str, Any]]:
    analyzer = ensure_analyzer()
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="agent-", dir=REPORT_DIR) as directory:
        json_path = Path(directory) / "analysis.json"
        trace_path = Path(directory) / "trace.json"
        subprocess.run(
            [
                str(analyzer),
                "agent",
                str(project_path),
                "--task",
                task,
                "--json",
                str(json_path),
                "--trace",
                str(trace_path),
            ],
            cwd=str(ROOT),
            check=True,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        return json.loads(json_path.read_text(encoding="utf-8")), json.loads(trace_path.read_text(encoding="utf-8"))


def generate_llm_report(data: dict[str, Any], payload: dict[str, Any]) -> str:
    model = str(payload.get("model") or os.environ.get("PROJECTAGENTCPP_MODEL") or os.environ.get("OPENAI_MODEL") or "")
    base_url = str(payload.get("base_url") or os.environ.get("PROJECTAGENTCPP_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1")
    style = str(payload.get("style") or "interview")
    messages = build_report_messages(data)
    messages.append(
        {
            "role": "user",
            "content": (
                f"{STYLE_PROMPTS.get(style, STYLE_PROMPTS['interview'])}\n"
                f"本次生成编号：{uuid.uuid4().hex[:12]}。\n"
                "请在不改变事实的前提下换一种自然表达方式，不要每次使用完全相同的句式。"
            ),
        }
    )
    client = LLMClient(
        LLMConfig(
            model=model,
            base_url=base_url,
            api_key_env=str(payload.get("api_key_env") or "OPENAI_API_KEY"),
            api_key=str(payload.get("api_key") or ""),
            timeout_seconds=90,
            temperature=float(payload.get("temperature") or 0.75),
        )
    )
    return client.chat(messages)


def generate_llm_answer(data: dict[str, Any], question: str, payload: dict[str, Any]) -> str:
    model = str(payload.get("model") or os.environ.get("PROJECTAGENTCPP_MODEL") or os.environ.get("OPENAI_MODEL") or "")
    base_url = str(payload.get("base_url") or os.environ.get("PROJECTAGENTCPP_BASE_URL") or os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1")
    messages = build_interview_messages(data, question)
    messages.append(
        {
            "role": "user",
            "content": (
                f"本次回答编号：{uuid.uuid4().hex[:12]}。\n"
                "请保持事实准确，但表达可以更自然、更像真实面试交流。"
            ),
        }
    )
    client = LLMClient(
        LLMConfig(
            model=model,
            base_url=base_url,
            api_key_env=str(payload.get("api_key_env") or "OPENAI_API_KEY"),
            api_key=str(payload.get("api_key") or ""),
            timeout_seconds=90,
            temperature=float(payload.get("temperature") or 0.7),
        )
    )
    return client.chat(messages)


def friendly_llm_error(error: Exception) -> str:
    message = str(error)
    if "Missing API key" in message:
        return "未配置 API Key"
    if "Missing model" in message:
        return "未配置模型"
    return f"LLM 请求失败：{message}"


def handle_analyze(
    payload: dict[str, Any],
    progress: ProgressCallback = noop_progress,
    should_cancel: CancelCallback = never_cancel,
) -> dict[str, Any]:
    project_path = safe_project_path(payload.get("project_path"))
    data = get_analysis(project_path, progress, should_cancel)
    llm_requested = bool(payload.get("use_llm"))
    used_llm = False
    llm_warning = ""
    check_cancel(should_cancel)
    if llm_requested:
        progress(58, "正在生成 LLM 报告")
        try:
            markdown = generate_llm_report(data, payload)
            used_llm = True
        except (LLMConfigurationError, LLMRequestError) as exc:
            llm_warning = f"{friendly_llm_error(exc)}，已自动回退到离线报告。"
            progress(70, llm_warning)
            markdown = generate_report(data)
    else:
        progress(58, "正在生成离线报告")
        markdown = generate_report(data)
    check_cancel(should_cancel)
    progress(88, "正在保存报告")
    prefix = f"{data.get('project_name', project_path.name)}_analysis"
    report_path, json_path = output_paths(prefix)
    report_path.write_text(markdown, encoding="utf-8")
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    history_item = save_history("analyze", project_path, "项目分析", markdown, data, used_llm)
    return {
        "ok": True,
        "analysis": data,
        "markdown": markdown,
        "talk_scripts": build_interview_scripts(data),
        "llm_requested": llm_requested,
        "used_llm": used_llm,
        "llm_warning": llm_warning,
        "report_path": str(report_path),
        "json_path": str(json_path),
        "history_item": history_item,
    }


def handle_agent(
    payload: dict[str, Any],
    progress: ProgressCallback = noop_progress,
    should_cancel: CancelCallback = never_cancel,
) -> dict[str, Any]:
    project_path = safe_project_path(payload.get("project_path"))
    task = str(payload.get("task") or "分析项目架构、核心模块和代码符号").strip()
    progress(16, "Planner 正在根据任务选择工具")
    check_cancel(should_cancel)
    progress(30, "Executor 正在执行工具链")
    analysis, trace = run_agent_trace(project_path, task)
    check_cancel(should_cancel)
    progress(76, "正在整理 Agent Trace")
    markdown = generate_report(analysis)
    combined = {"analysis": analysis, "trace": trace}
    report_path, json_path = output_paths(f"{project_path.name}_agent")
    report_path.write_text(markdown, encoding="utf-8")
    json_path.write_text(json.dumps(combined, ensure_ascii=False, indent=2), encoding="utf-8")
    history_item = save_history("agent", project_path, "Agent Trace", markdown, combined)
    return {
        "ok": True,
        "analysis": analysis,
        "trace": trace,
        "markdown": markdown,
        "talk_scripts": build_interview_scripts(analysis),
        "history_item": history_item,
    }


def handle_ask(
    payload: dict[str, Any],
    progress: ProgressCallback = noop_progress,
    should_cancel: CancelCallback = never_cancel,
) -> dict[str, Any]:
    project_path = safe_project_path(payload.get("project_path"))
    question = str(payload.get("question") or "").strip()
    if not question:
        raise ValueError("请输入面试问题。")
    data = get_analysis(project_path, progress, should_cancel)
    llm_requested = bool(payload.get("use_llm"))
    used_llm = False
    llm_warning = ""
    check_cancel(should_cancel)
    progress(58, "正在生成面试回答")
    if llm_requested:
        try:
            markdown = generate_llm_answer(data, question, payload)
            used_llm = True
        except (LLMConfigurationError, LLMRequestError) as exc:
            llm_warning = f"{friendly_llm_error(exc)}，已自动回退到离线回答。"
            progress(70, llm_warning)
            markdown = generate_offline_interview_answer(data, question)
    else:
        markdown = generate_offline_interview_answer(data, question)
    check_cancel(should_cancel)
    report_path, _ = output_paths(f"{data.get('project_name', project_path.name)}_answer")
    report_path.write_text(markdown, encoding="utf-8")
    history_item = save_history(
        "ask",
        project_path,
        "面试问答",
        markdown,
        {"question": question, "analysis": data},
        used_llm,
    )
    return {
        "ok": True,
        "markdown": markdown,
        "llm_requested": llm_requested,
        "used_llm": used_llm,
        "llm_warning": llm_warning,
        "report_path": str(report_path),
        "history_item": history_item,
    }


def handle_diagnose(
    payload: dict[str, Any],
    progress: ProgressCallback = noop_progress,
    should_cancel: CancelCallback = never_cancel,
) -> dict[str, Any]:
    project_path = safe_project_path(payload.get("project_path"))
    mode = str(payload.get("mode") or "dry")
    build_dir = Path(
        str(payload.get("build_dir") or REPORT_DIR / "diagnostics" / "builds" / project_path.name)
    ).expanduser().resolve()
    skip_all = mode == "dry"
    cmake_args = list(payload.get("cmake_args") or [])
    if mode == "build-test" and not cmake_args:
        cmake_args = ["-DMINIREDIS_ENABLE_INTEGRATION_TESTS=OFF"]
    progress(8, "正在准备诊断任务")
    result = run_diagnostics(
        project_path=project_path,
        build_dir=build_dir,
        cmake_args=cmake_args,
        build_args=list(payload.get("build_args") or []),
        ctest_args=list(payload.get("ctest_args") or []),
        skip_configure=bool(payload.get("skip_configure")) or skip_all,
        skip_build=bool(payload.get("skip_build")) or skip_all,
        skip_test=bool(payload.get("skip_test")) or skip_all,
        start_command=str(payload.get("start_command") or "").strip() or None,
        startup_seconds=float(payload.get("startup_seconds") or 1.0),
        benchmark_command=str(payload.get("benchmark_command") or "").strip() or None,
        stats_url=str(payload.get("stats_url") or "").strip() or None,
        timeout_seconds=int(payload.get("timeout") or 180),
        on_progress=progress,
        should_cancel=should_cancel,
    )
    check_cancel(should_cancel)
    markdown = generate_diagnostic_report(result)
    diagnostic_data = json.loads(result_to_json(result))
    report_path, json_path = output_paths(f"{project_path.name}_diagnostic")
    report_path.write_text(markdown, encoding="utf-8")
    json_path.write_text(result_to_json(result), encoding="utf-8")
    history_item = save_history("diagnose", project_path, "服务诊断", markdown, diagnostic_data)
    return {
        "ok": True,
        "diagnostic": diagnostic_data,
        "markdown": markdown,
        "report_path": str(report_path),
        "json_path": str(json_path),
        "history_item": history_item,
    }


def handle_ast(
    payload: dict[str, Any],
    progress: ProgressCallback = noop_progress,
    should_cancel: CancelCallback = never_cancel,
) -> dict[str, Any]:
    project_path = safe_project_path(payload.get("project_path"))
    dump_dir = Path(
        str(payload.get("dump_dir") or REPORT_DIR / "ast" / "dumps" / project_path.name)
    ).expanduser().resolve()
    progress(16, "正在定位 compile_commands.json")
    check_cancel(should_cancel)
    progress(34, "正在执行 Clang AST 分析")
    result = analyze_ast(
        project_path=project_path,
        compile_db=str(payload.get("compile_db") or "").strip() or None,
        clang_binary=str(payload.get("clang_bin") or "clang++"),
        dump_dir=dump_dir,
        max_files=int(payload.get("max_files") or 3),
        timeout=int(payload.get("timeout") or 60),
        ast_json_path=str(payload.get("ast_json") or "").strip() or None,
    )
    check_cancel(should_cancel)
    progress(84, "正在生成 AST 报告")
    markdown = generate_ast_report(result)
    ast_data = json.loads(ast_result_to_json(result))
    report_path, json_path = output_paths(f"{project_path.name}_ast")
    report_path.write_text(markdown, encoding="utf-8")
    json_path.write_text(ast_result_to_json(result), encoding="utf-8")
    history_item = save_history("ast", project_path, "Clang AST", markdown, ast_data)
    return {
        "ok": True,
        "ast": ast_data,
        "markdown": markdown,
        "report_path": str(report_path),
        "json_path": str(json_path),
        "history_item": history_item,
    }


ACTION_HANDLERS: dict[str, ActionHandler] = {
    "analyze": handle_analyze,
    "agent": handle_agent,
    "ask": handle_ask,
    "diagnose": handle_diagnose,
    "ast": handle_ast,
}

SYNC_API_HANDLERS = {f"/api/{name}": handler for name, handler in ACTION_HANDLERS.items()}


@dataclass
class JobRecord:
    id: str
    action: str
    status: str = "queued"
    progress: int = 0
    message: str = "任务已创建"
    logs: list[str] = field(default_factory=list)
    result: dict[str, Any] | None = None
    error: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().astimezone().isoformat(timespec="seconds"))
    cancel_event: threading.Event = field(default_factory=threading.Event, repr=False)

    def public(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "action": self.action,
            "status": self.status,
            "progress": self.progress,
            "message": self.message,
            "logs": self.logs[-100:],
            "result": self.result if self.status == "completed" else None,
            "error": self.error,
            "created_at": self.created_at,
        }


class JobManager:
    def __init__(self) -> None:
        self._jobs: dict[str, JobRecord] = {}
        self._lock = threading.Lock()

    def start(self, action: str, payload: dict[str, Any]) -> JobRecord:
        if action not in ACTION_HANDLERS:
            raise ValueError(f"Unknown action: {action}")
        job = JobRecord(id=uuid.uuid4().hex, action=action)
        with self._lock:
            self._jobs[job.id] = job
            self._trim_locked()
        thread = threading.Thread(target=self._run, args=(job, payload), daemon=True)
        thread.start()
        return job

    def get(self, job_id: str) -> JobRecord | None:
        with self._lock:
            return self._jobs.get(job_id)

    def cancel(self, job_id: str) -> JobRecord | None:
        with self._lock:
            job = self._jobs.get(job_id)
            if job and job.status in {"queued", "running", "cancelling"}:
                job.cancel_event.set()
                job.status = "cancelling"
                job.message = "正在取消任务"
                job.logs.append("收到取消请求")
            return job

    def _update(self, job: JobRecord, value: int, message: str) -> None:
        if job.cancel_event.is_set():
            raise JobCancelled("任务已取消")
        with self._lock:
            job.progress = max(job.progress, min(99, int(value)))
            job.message = message
            if not job.logs or job.logs[-1] != message:
                job.logs.append(message)

    def _run(self, job: JobRecord, payload: dict[str, Any]) -> None:
        with self._lock:
            job.status = "running"
            job.message = "任务开始执行"
            job.logs.append(job.message)
        try:
            result = ACTION_HANDLERS[job.action](
                payload,
                lambda value, message: self._update(job, value, message),
                job.cancel_event.is_set,
            )
            if job.cancel_event.is_set():
                raise JobCancelled("任务已取消")
            with self._lock:
                job.status = "completed"
                job.progress = 100
                job.message = "任务已完成"
                job.logs.append(job.message)
                job.result = result
        except JobCancelled as exc:
            with self._lock:
                job.status = "cancelled"
                job.message = str(exc)
                job.logs.append(str(exc))
        except subprocess.CalledProcessError as exc:
            detail = (exc.stderr or exc.stdout or "Command failed").strip()
            self._fail(job, detail)
        except Exception as exc:  # noqa: BLE001
            self._fail(job, str(exc))

    def _fail(self, job: JobRecord, error: str) -> None:
        with self._lock:
            job.status = "failed"
            job.message = "任务执行失败"
            job.error = error
            job.logs.append(error)

    def _trim_locked(self) -> None:
        if len(self._jobs) <= 60:
            return
        finished = [job for job in self._jobs.values() if job.status in {"completed", "failed", "cancelled"}]
        for job in sorted(finished, key=lambda item: item.created_at)[: len(self._jobs) - 60]:
            self._jobs.pop(job.id, None)


JOB_MANAGER = JobManager()


class WebUIHandler(BaseHTTPRequestHandler):
    server_version = "ProjectAgentCppWeb/0.2.1"

    def do_GET(self) -> None:
        path = unquote(urlsplit(self.path).path)
        try:
            if path == "/api/health":
                json_response(self, {"ok": True, "root": str(ROOT), "version": "0.2.1"})
                return
            if path == "/api/history":
                json_response(self, {"ok": True, "items": list_history()})
                return
            if path.startswith("/api/history/"):
                json_response(self, load_history(path.rsplit("/", 1)[-1]))
                return
            if path.startswith("/api/jobs/"):
                job = JOB_MANAGER.get(path.rsplit("/", 1)[-1])
                if job is None:
                    json_response(self, {"ok": False, "error": "Job not found."}, status=404)
                else:
                    json_response(self, {"ok": True, "job": job.public()})
                return
            self._serve_static(path)
        except FileNotFoundError as exc:
            json_response(self, {"ok": False, "error": str(exc)}, status=404)
        except Exception as exc:  # noqa: BLE001
            json_response(self, {"ok": False, "error": str(exc)}, status=400)

    def do_POST(self) -> None:
        path = unquote(urlsplit(self.path).path)
        try:
            payload = read_json_body(self)
            if path == "/api/jobs":
                action = str(payload.get("action") or "")
                action_payload = payload.get("payload") or {}
                if not isinstance(action_payload, dict):
                    raise ValueError("Job payload must be an object.")
                job = JOB_MANAGER.start(action, action_payload)
                json_response(self, {"ok": True, "job": job.public()}, status=202)
                return
            cancel_match = re.fullmatch(r"/api/jobs/([A-Za-z0-9_-]+)/cancel", path)
            if cancel_match:
                job = JOB_MANAGER.cancel(cancel_match.group(1))
                if job is None:
                    json_response(self, {"ok": False, "error": "Job not found."}, status=404)
                else:
                    json_response(self, {"ok": True, "job": job.public()})
                return
            handler = SYNC_API_HANDLERS.get(path)
            if handler is None:
                json_response(self, {"ok": False, "error": "Unknown API endpoint."}, status=404)
                return
            json_response(self, handler(payload, noop_progress, never_cancel))
        except subprocess.CalledProcessError as exc:
            json_response(
                self,
                {"ok": False, "error": "Command failed.", "stdout": exc.stdout, "stderr": exc.stderr},
                status=500,
            )
        except (LLMConfigurationError, LLMRequestError) as exc:
            json_response(self, {"ok": False, "error": str(exc)}, status=502)
        except Exception as exc:  # noqa: BLE001
            json_response(self, {"ok": False, "error": str(exc)}, status=400)

    def _serve_static(self, path: str) -> None:
        if path == "/":
            path = "/index.html"
        file_path = (STATIC_DIR / path.lstrip("/")).resolve()
        if STATIC_DIR not in file_path.parents and file_path != STATIC_DIR:
            self.send_error(HTTPStatus.FORBIDDEN)
            return
        if not file_path.exists() or not file_path.is_file():
            self.send_error(HTTPStatus.NOT_FOUND)
            return
        content = file_path.read_bytes()
        content_type = mimetypes.guess_type(str(file_path))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(content)))
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("[webui] " + format % args + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="ProjectAgentCpp local Web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), WebUIHandler)
    server.daemon_threads = True
    print(f"ProjectAgentCpp Web UI: http://{args.host}:{args.port}")
    if args.host not in {"127.0.0.1", "localhost", "::1"}:
        print("Warning: command execution APIs are exposed beyond localhost.", file=sys.stderr)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
