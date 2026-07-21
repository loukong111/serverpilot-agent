from __future__ import annotations

import argparse
import json
import re
import time
import urllib.request
from typing import Any


def request_json(base_url: str, path: str, payload: dict[str, Any] | None = None) -> dict[str, Any]:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        base_url.rstrip("/") + path,
        data=data,
        headers={"Content-Type": "application/json"} if data is not None else {},
        method="POST" if data is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return json.loads(response.read().decode("utf-8"))


def run_job(base_url: str, action: str, payload: dict[str, Any], timeout: float = 120) -> dict[str, Any]:
    started = request_json(base_url, "/api/jobs", {"action": action, "payload": payload})
    job_id = started["job"]["id"]
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        job = request_json(base_url, f"/api/jobs/{job_id}")["job"]
        if job["status"] in {"completed", "failed", "cancelled"}:
            return job
        time.sleep(0.2)
    raise TimeoutError(f"Job {job_id} did not finish in {timeout}s")


def require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base-url", default="http://127.0.0.1:8876")
    parser.add_argument("--project", default="/home/wcl/project/miniredis")
    args = parser.parse_args()
    common = {"project_path": args.project}

    health = request_json(args.base_url, "/api/health")
    require(health.get("ok") is True, "health check failed")
    version = str(health.get("version", ""))
    require(re.fullmatch(r"\d+\.\d+\.\d+", version) is not None, f"invalid server version: {version}")
    print(f"health: ok (version {version})")

    unavailable_llm = {
        "use_llm": True,
        "model": "smoke-test",
        "api_key": "smoke-test",
        "base_url": "http://127.0.0.1:1",
    }
    analyze = run_job(args.base_url, "analyze", {**common, **unavailable_llm})
    require(analyze["status"] == "completed", f"analyze failed: {analyze['error']}")
    require(analyze["result"]["used_llm"] is False, "analyze did not fall back without API key")
    require(bool(analyze["result"]["llm_warning"]), "analyze fallback warning is missing")
    print("analyze with LLM fallback: ok")

    ask = run_job(
        args.base_url,
        "ask",
        {**common, **unavailable_llm, "question": "这个项目的架构亮点是什么？"},
    )
    require(ask["status"] == "completed", f"ask failed: {ask['error']}")
    require(ask["result"]["used_llm"] is False, "ask did not fall back without API key")
    print("interview fallback and analysis cache: ok")

    agent = run_job(args.base_url, "agent", {**common, "task": "只检查 CMake 构建"})
    require(agent["status"] == "completed", f"agent failed: {agent['error']}")
    tools = [step["tool"] for step in agent["result"]["trace"]["steps"]]
    require(tools == ["scan_project", "analyze_cmake", "evaluate_project", "generate_json"], f"unexpected plan: {tools}")
    print("task-aware agent trace: ok")

    diagnose = run_job(args.base_url, "diagnose", {**common, "mode": "dry"})
    require(diagnose["status"] == "completed", f"diagnose failed: {diagnose['error']}")
    require(diagnose["result"]["diagnostic"]["success"] is True, "dry diagnosis status is missing")
    require(diagnose["result"]["diagnostic"]["failed_steps"] == [], "dry diagnosis has failed steps")
    require(
        diagnose["result"]["diagnostic"]["verification_status"] == "incomplete",
        "dry diagnosis should not claim complete verification",
    )
    require(diagnose["result"]["diagnostic"]["tests_found"] is None, "skipped tests should be unknown")
    print("diagnose dry run: ok")

    ast = run_job(
        args.base_url,
        "ast",
        {**common, "ast_json": "examples/sample_ast.json", "max_files": 1, "timeout": 10},
    )
    require(ast["status"] == "completed", f"ast failed: {ast['error']}")
    require(ast["result"]["ast"]["analyzed_files"], "AST sample produced no analyzed files")
    print("AST sample: ok")

    history = request_json(args.base_url, "/api/history")
    require(len(history.get("items", [])) >= 5, "history entries are missing")
    restored = request_json(args.base_url, f"/api/history/{history['items'][0]['id']}")
    require(restored.get("markdown"), "history report could not be restored")
    print("history list and restore: ok")

    invalid = run_job(args.base_url, "analyze", {"project_path": "/path/that/does/not/exist"})
    require(invalid["status"] == "failed", "invalid project path should fail")
    require("有效目录" in invalid["error"], "invalid path error is not actionable")
    print("invalid path error: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
