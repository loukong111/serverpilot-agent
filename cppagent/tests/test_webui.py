from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent.project_agent.diagnostics import DiagnosticResult, DiagnosticStep
from agent.project_agent.llm_client import LLMConfigurationError, LLMRequestError
from webui import server


SAMPLE_ANALYSIS = {
    "project_name": "sample",
    "root": "/tmp/sample",
    "has_readme": True,
    "has_cmake": True,
    "directories": {"src": True, "include": True, "tests": True, "docs": False},
    "files": {"source_count": 2, "header_count": 1, "test_count": 1},
    "cmake": {
        "cpp_standard": "20",
        "executables": ["sample"],
        "libraries": [],
        "tests": ["sample_tests"],
        "packages": [],
    },
    "entry_points": ["src/main.cpp"],
    "modules": [{"name": "network", "confidence": 0.8, "files": ["src/main.cpp"], "evidence": ["socket"]}],
    "strengths": ["具备基础工程结构。"],
    "risks": [],
    "clang": {},
}


class WebUIFallbackTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.project = self.root / "sample"
        self.project.mkdir()
        self.source = self.project / "src" / "value.cpp"
        self.source.parent.mkdir()
        self.source.write_text("int value() { return 1; }\n", encoding="utf-8")
        self.report_dir = self.root / "reports"
        self.history_dir = self.report_dir / "history"
        self.proposal_dir = self.report_dir / "coding" / "proposals"
        self.backup_dir = self.report_dir / "coding" / "backups"
        self.report_patch = mock.patch.object(server, "REPORT_DIR", self.report_dir)
        self.history_patch = mock.patch.object(server, "HISTORY_DIR", self.history_dir)
        self.proposal_patch = mock.patch.object(server, "PROPOSAL_DIR", self.proposal_dir)
        self.backup_patch = mock.patch.object(server, "BACKUP_DIR", self.backup_dir)
        self.report_patch.start()
        self.history_patch.start()
        self.proposal_patch.start()
        self.backup_patch.start()

    def tearDown(self) -> None:
        self.backup_patch.stop()
        self.proposal_patch.stop()
        self.history_patch.stop()
        self.report_patch.stop()
        self.temp_dir.cleanup()

    @mock.patch.object(server, "get_analysis", return_value=SAMPLE_ANALYSIS)
    @mock.patch.object(server, "generate_llm_report", side_effect=LLMConfigurationError("Missing API key."))
    def test_analyze_falls_back_when_llm_is_not_configured(self, _llm: mock.Mock, _analysis: mock.Mock) -> None:
        result = server.handle_analyze(
            {"project_path": str(self.project), "use_llm": True},
            server.noop_progress,
            server.never_cancel,
        )

        self.assertTrue(result["ok"])
        self.assertTrue(result["llm_requested"])
        self.assertFalse(result["used_llm"])
        self.assertIn("离线报告", result["llm_warning"])
        self.assertTrue(Path(result["history_item"]["report_path"]).exists())

    @mock.patch.object(server, "get_analysis", return_value=SAMPLE_ANALYSIS)
    @mock.patch.object(server, "generate_llm_answer", side_effect=LLMRequestError("network unavailable"))
    def test_ask_falls_back_when_llm_request_fails(self, _llm: mock.Mock, _analysis: mock.Mock) -> None:
        result = server.handle_ask(
            {
                "project_path": str(self.project),
                "question": "项目亮点是什么？",
                "use_llm": True,
            },
            server.noop_progress,
            server.never_cancel,
        )

        self.assertTrue(result["ok"])
        self.assertFalse(result["used_llm"])
        self.assertIn("离线回答", result["llm_warning"])
        self.assertIn("项目亮点是什么", result["markdown"])

    @mock.patch.object(server, "get_analysis", return_value=SAMPLE_ANALYSIS)
    @mock.patch.object(server.LLMClient, "chat")
    def test_coding_proposal_can_be_applied_and_rolled_back(
        self, chat: mock.Mock, _analysis: mock.Mock
    ) -> None:
        patch = (
            "diff --git a/src/value.cpp b/src/value.cpp\n"
            "--- a/src/value.cpp\n"
            "+++ b/src/value.cpp\n"
            "@@ -1 +1 @@\n"
            "-int value() { return 1; }\n"
            "+int value() { return 2; }\n"
        )
        chat.return_value = json.dumps(
            {
                "summary": "调整返回值",
                "plan": ["修改实现", "执行测试"],
                "risks": [],
                "tests": ["ctest --test-dir build"],
                "patch": patch,
            },
            ensure_ascii=False,
        )

        proposed = server.handle_coding(
            {
                "project_path": str(self.project),
                "task": "把 value 返回值改为 2",
                "model": "test-model",
                "api_key": "test-key",
            },
            server.noop_progress,
            server.never_cancel,
        )
        self.assertEqual("pending", proposed["proposal"]["status"])

        applied = server.handle_coding_apply({"proposal_id": proposed["proposal"]["id"]})
        self.assertEqual("applied", applied["proposal"]["status"])
        self.assertIn("return 2", self.source.read_text(encoding="utf-8"))

        rolled_back = server.handle_coding_rollback({"proposal_id": proposed["proposal"]["id"]})
        self.assertEqual("rolled_back", rolled_back["proposal"]["status"])
        self.assertIn("return 1", self.source.read_text(encoding="utf-8"))

    @mock.patch.object(server, "get_analysis", return_value=SAMPLE_ANALYSIS)
    @mock.patch.object(server.LLMClient, "chat")
    def test_failed_diagnostic_creates_chained_repair_proposal(
        self, chat: mock.Mock, _analysis: mock.Mock
    ) -> None:
        first_patch = (
            "diff --git a/src/value.cpp b/src/value.cpp\n"
            "--- a/src/value.cpp\n+++ b/src/value.cpp\n"
            "@@ -1 +1 @@\n"
            "-int value() { return 1; }\n+int value() { return 2; }\n"
        )
        chat.return_value = json.dumps(
            {
                "summary": "第一轮修改",
                "plan": ["修改返回值"],
                "risks": [],
                "tests": ["cmake --build build"],
                "patch": first_patch,
            },
            ensure_ascii=False,
        )
        proposed = server.handle_coding(
            {
                "project_path": str(self.project),
                "task": "修改 value",
                "model": "test-model",
                "api_key": "test-key",
            }
        )
        parent_id = proposed["proposal"]["id"]
        server.handle_coding_apply({"proposal_id": parent_id})

        diagnostic = {
            "project_path": str(self.project),
            "build_dir": str(self.root / "build"),
            "success": False,
            "failed_steps": ["build"],
            "steps": [
                {
                    "name": "build",
                    "command": ["cmake", "--build", "build"],
                    "success": False,
                    "skipped": False,
                    "exit_code": 2,
                    "stdout": "",
                    "stderr": "value.cpp: expected return value 3",
                    "observation": "command failed",
                }
            ],
        }
        diagnostic_history = server.save_history(
            "diagnose",
            self.project,
            "项目诊断",
            "# 构建失败\n",
            diagnostic,
        )
        repair_patch = (
            "diff --git a/src/value.cpp b/src/value.cpp\n"
            "--- a/src/value.cpp\n+++ b/src/value.cpp\n"
            "@@ -1 +1 @@\n"
            "-int value() { return 2; }\n+int value() { return 3; }\n"
        )
        chat.return_value = json.dumps(
            {
                "summary": "修复构建错误",
                "plan": ["根据编译错误调整实现"],
                "risks": [],
                "tests": ["cmake --build build"],
                "patch": repair_patch,
            },
            ensure_ascii=False,
        )

        repair = server.handle_coding_repair(
            {
                "project_path": str(self.project),
                "proposal_id": parent_id,
                "diagnostic_history_id": diagnostic_history["id"],
                "model": "test-model",
                "api_key": "test-key",
            }
        )
        repair_id = repair["proposal"]["id"]
        self.assertEqual("repair", repair["proposal"]["kind"])
        self.assertEqual(2, repair["proposal"]["round"])
        self.assertEqual(parent_id, repair["proposal"]["parent_id"])

        server.handle_coding_apply({"proposal_id": repair_id})
        self.assertIn("return 3", self.source.read_text(encoding="utf-8"))
        server.handle_coding_rollback({"proposal_id": repair_id})
        self.assertIn("return 2", self.source.read_text(encoding="utf-8"))
        server.handle_coding_rollback({"proposal_id": parent_id})
        self.assertIn("return 1", self.source.read_text(encoding="utf-8"))

    @mock.patch.object(server, "run_diagnostics")
    def test_diagnostic_result_exposes_top_level_failure(self, run: mock.Mock) -> None:
        run.return_value = DiagnosticResult(
            project_path=str(self.project),
            build_dir=str(self.root / "build"),
            steps=[
                DiagnosticStep(
                    name="build",
                    command=["cmake", "--build", "build"],
                    success=False,
                    exit_code=2,
                    stderr="compile failed",
                    observation="command failed",
                )
            ],
        )
        result = server.handle_diagnose({"project_path": str(self.project), "mode": "build-test"})

        self.assertFalse(result["diagnostic"]["success"])
        self.assertEqual(["build"], result["diagnostic"]["failed_steps"])
        self.assertTrue(result["diagnostic"]["repairable"])
        self.assertEqual("failed", result["diagnostic"]["verification_status"])

    @mock.patch.object(server, "run_diagnostics")
    def test_no_ctest_cases_are_reported_as_incomplete(self, run: mock.Mock) -> None:
        run.return_value = DiagnosticResult(
            project_path=str(self.project),
            build_dir=str(self.root / "build"),
            steps=[
                DiagnosticStep(name="configure", success=True),
                DiagnosticStep(name="build", success=True),
                DiagnosticStep(name="test", success=True, stdout="No tests were found!!!"),
            ],
        )
        result = server.handle_diagnose({"project_path": str(self.project), "mode": "build-test"})

        self.assertTrue(result["diagnostic"]["success"])
        self.assertFalse(result["diagnostic"]["tests_found"])
        self.assertFalse(result["diagnostic"]["repairable"])
        self.assertEqual("incomplete", result["diagnostic"]["verification_status"])

    @mock.patch.object(server, "run_diagnostics")
    def test_benchmark_failure_does_not_trigger_code_repair(self, run: mock.Mock) -> None:
        run.return_value = DiagnosticResult(
            project_path=str(self.project),
            build_dir=str(self.root / "build"),
            steps=[
                DiagnosticStep(name="configure", success=True),
                DiagnosticStep(name="build", success=True),
                DiagnosticStep(name="test", success=True, stdout="100% tests passed"),
                DiagnosticStep(name="benchmark", success=False, exit_code=1),
            ],
        )
        result = server.handle_diagnose({"project_path": str(self.project), "mode": "build-test"})

        self.assertFalse(result["diagnostic"]["success"])
        self.assertFalse(result["diagnostic"]["repairable"])
        self.assertEqual([], result["diagnostic"]["repairable_steps"])


if __name__ == "__main__":
    unittest.main()
