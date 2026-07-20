from __future__ import annotations

import argparse
import json
import mimetypes
import os
import subprocess
import sys
import uuid
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = Path(__file__).resolve().parent / "static"
REPORT_DIR = ROOT / "reports" / "web"

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
    LLMConfig,
    LLMConfigurationError,
    LLMRequestError,
    LLMClient,
)
from agent.project_agent.prompts import build_interview_messages, build_report_messages  # noqa: E402


STYLE_PROMPTS = {
    "interview": "这次报告请偏面试表达，强调候选人如何讲清楚项目价值、技术亮点和可追问点。",
    "resume": "这次报告请偏简历表达，强调可量化的项目亮点、工程能力和简历 bullet 写法。",
    "deep": "这次报告请偏深挖问题，强调架构边界、潜在风险、测试缺口和下一步改进路线。",
}


def ensure_analyzer() -> Path:
    analyzer = default_analyzer_path()
    if analyzer.exists():
        return analyzer
    raise RuntimeError("cpp_analyzer is not built. Run `cmake -S . -B build && cmake --build build` first.")


def json_response(handler: BaseHTTPRequestHandler, payload: dict[str, Any], status: int = 200) -> None:
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def read_json_body(handler: BaseHTTPRequestHandler) -> dict[str, Any]:
    length = int(handler.headers.get("Content-Length", "0"))
    if length <= 0:
        return {}
    body = handler.rfile.read(length).decode("utf-8")
    return json.loads(body)


def safe_project_path(value: Any) -> Path:
    project_path = Path(str(value or "")).expanduser().resolve()
    if not project_path.exists() or not project_path.is_dir():
        raise ValueError(f"Project path is not a directory: {project_path}")
    return project_path


def output_paths(prefix: str) -> tuple[Path, Path]:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    return REPORT_DIR / f"{prefix}.md", REPORT_DIR / f"{prefix}.json"


def run_agent_trace(project_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    analyzer = ensure_analyzer()
    prefix = project_path.name
    json_path = REPORT_DIR / f"{prefix}_agent.json"
    trace_path = REPORT_DIR / f"{prefix}_trace.json"
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [str(analyzer), "agent", str(project_path), "--json", str(json_path), "--trace", str(trace_path)],
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
    style_prompt = STYLE_PROMPTS.get(style, STYLE_PROMPTS["interview"])
    messages = build_report_messages(data)
    messages.append(
        {
            "role": "user",
            "content": (
                f"{style_prompt}\n"
                f"本次生成编号：{uuid.uuid4().hex[:12]}。\n"
                "请在不改变事实的前提下，换一种自然表达方式；不要每次都使用完全相同的句式。"
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


def handle_analyze(payload: dict[str, Any]) -> dict[str, Any]:
    project_path = safe_project_path(payload.get("project_path"))
    analyzer = ensure_analyzer()
    data = run_analyzer(analyzer, project_path)
    used_llm = bool(payload.get("use_llm"))
    markdown = generate_llm_report(data, payload) if used_llm else generate_report(data)
    report_path, json_path = output_paths(f"{data.get('project_name', project_path.name)}_analysis")
    report_path.write_text(markdown, encoding="utf-8")
    json_path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return {
        "ok": True,
        "analysis": data,
        "markdown": markdown,
        "talk_scripts": build_interview_scripts(data),
        "used_llm": used_llm,
        "report_path": str(report_path),
        "json_path": str(json_path),
    }


def handle_agent(payload: dict[str, Any]) -> dict[str, Any]:
    project_path = safe_project_path(payload.get("project_path"))
    analysis, trace = run_agent_trace(project_path)
    return {
        "ok": True,
        "analysis": analysis,
        "trace": trace,
        "talk_scripts": build_interview_scripts(analysis),
    }


def handle_ask(payload: dict[str, Any]) -> dict[str, Any]:
    project_path = safe_project_path(payload.get("project_path"))
    question = str(payload.get("question") or "").strip()
    if not question:
        raise ValueError("Question is required.")
    analyzer = ensure_analyzer()
    data = run_analyzer(analyzer, project_path)
    used_llm = bool(payload.get("use_llm"))
    markdown = generate_llm_answer(data, question, payload) if used_llm else generate_offline_interview_answer(data, question)
    report_path, _ = output_paths(f"{data.get('project_name', project_path.name)}_answer")
    report_path.write_text(markdown, encoding="utf-8")
    return {
        "ok": True,
        "markdown": markdown,
        "used_llm": used_llm,
        "report_path": str(report_path),
    }


def handle_diagnose(payload: dict[str, Any]) -> dict[str, Any]:
    project_path = safe_project_path(payload.get("project_path"))
    mode = str(payload.get("mode") or "dry")
    build_dir = Path(str(payload.get("build_dir") or REPORT_DIR / "diagnostics" / "builds" / project_path.name)).expanduser().resolve()
    skip_all = mode == "dry"
    cmake_args = list(payload.get("cmake_args") or [])
    if mode == "build-test" and not cmake_args:
        cmake_args = ["-DMINIREDIS_ENABLE_INTEGRATION_TESTS=OFF"]
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
    )
    result.suggestions = result.suggestions or []
    markdown = generate_diagnostic_report(result)
    report_path, json_path = output_paths(f"{project_path.name}_diagnostic")
    report_path.write_text(markdown, encoding="utf-8")
    json_path.write_text(result_to_json(result), encoding="utf-8")
    return {
        "ok": True,
        "diagnostic": json.loads(result_to_json(result)),
        "markdown": markdown,
        "report_path": str(report_path),
        "json_path": str(json_path),
    }


def handle_ast(payload: dict[str, Any]) -> dict[str, Any]:
    project_path = safe_project_path(payload.get("project_path"))
    dump_dir = Path(str(payload.get("dump_dir") or REPORT_DIR / "ast" / "dumps" / project_path.name)).expanduser().resolve()
    result = analyze_ast(
        project_path=project_path,
        compile_db=str(payload.get("compile_db") or "").strip() or None,
        clang_binary=str(payload.get("clang_bin") or "clang++"),
        dump_dir=dump_dir,
        max_files=int(payload.get("max_files") or 3),
        timeout=int(payload.get("timeout") or 60),
        ast_json_path=str(payload.get("ast_json") or "").strip() or None,
    )
    markdown = generate_ast_report(result)
    report_path, json_path = output_paths(f"{project_path.name}_ast")
    report_path.write_text(markdown, encoding="utf-8")
    json_path.write_text(ast_result_to_json(result), encoding="utf-8")
    return {
        "ok": True,
        "ast": json.loads(ast_result_to_json(result)),
        "markdown": markdown,
        "report_path": str(report_path),
        "json_path": str(json_path),
    }


API_HANDLERS = {
    "/api/analyze": handle_analyze,
    "/api/agent": handle_agent,
    "/api/ask": handle_ask,
    "/api/diagnose": handle_diagnose,
    "/api/ast": handle_ast,
}


class WebUIHandler(BaseHTTPRequestHandler):
    server_version = "ProjectAgentCppWeb/0.1"

    def do_GET(self) -> None:
        if self.path == "/api/health":
            json_response(self, {"ok": True, "root": str(ROOT)})
            return
        path = self.path.split("?", 1)[0]
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

    def do_POST(self) -> None:
        handler = API_HANDLERS.get(self.path)
        if handler is None:
            json_response(self, {"ok": False, "error": "Unknown API endpoint."}, status=404)
            return
        try:
            payload = read_json_body(self)
            result = handler(payload)
            json_response(self, result)
        except subprocess.CalledProcessError as exc:
            json_response(
                self,
                {
                    "ok": False,
                    "error": "Command failed.",
                    "stdout": exc.stdout,
                    "stderr": exc.stderr,
                },
                status=500,
            )
        except (LLMConfigurationError, LLMRequestError) as exc:
            json_response(self, {"ok": False, "error": str(exc)}, status=502)
        except Exception as exc:  # noqa: BLE001
            json_response(self, {"ok": False, "error": str(exc)}, status=400)

    def log_message(self, format: str, *args: Any) -> None:
        sys.stderr.write("[webui] " + format % args + "\n")


def main() -> int:
    parser = argparse.ArgumentParser(description="ProjectAgentCpp local Web UI")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), WebUIHandler)
    print(f"ProjectAgentCpp Web UI: http://{args.host}:{args.port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
