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

    @mock.patch.object(server, "get_analysis")
    def test_coding_rejects_missing_llm_config_before_analysis(
        self, analysis: mock.Mock
    ) -> None:
        environment = {
            "OPENAI_API_KEY": "",
            "OPENAI_MODEL": "",
            "PROJECTAGENTCPP_MODEL": "",
        }
        with mock.patch.dict(server.os.environ, environment, clear=False):
            with self.assertRaisesRegex(LLMConfigurationError, "配置模型"):
                server.handle_coding(
                    {"project_path": str(self.project), "task": "修改 value"},
                    server.noop_progress,
                    server.never_cancel,
                )

        analysis.assert_not_called()

    @mock.patch.object(server, "get_analysis")
    def test_coding_rejects_legacy_vague_task_before_analysis(
        self, analysis: mock.Mock
    ) -> None:
        with self.assertRaisesRegex(ValueError, "任务描述过于宽泛"):
            server.handle_coding(
                {
                    "project_path": str(self.project),
                    "task": "为项目补充一个小型功能，并添加对应单元测试",
                    "provider": "ollama",
                }
            )

        analysis.assert_not_called()

    @mock.patch.object(server, "get_analysis")
    def test_coding_routes_diagnostic_only_task_before_analysis(
        self, analysis: mock.Mock
    ) -> None:
        with self.assertRaisesRegex(ValueError, "请切换到「诊断」模式"):
            server.handle_coding(
                {
                    "project_path": str(self.project),
                    "task": "帮我做一下测试看看能不能正常运行",
                    "provider": "ollama",
                }
            )

        analysis.assert_not_called()

    def test_diagnostic_intent_allows_explicit_code_changes(self) -> None:
        self.assertTrue(server.is_diagnostic_only_task("帮我测试看看能不能正常运行"))
        self.assertFalse(server.is_diagnostic_only_task("修复测试失败并补充单元测试"))

    def test_llm_configuration_status_does_not_expose_key(self) -> None:
        environment = {
            "OPENAI_API_KEY": "secret-test-key",
            "OPENROUTER_API_KEY": "",
            "PROJECTAGENTCPP_MODEL": "test-model",
        }
        with mock.patch.dict(server.os.environ, environment, clear=False):
            status = server.llm_configuration_status()

        self.assertEqual(
            {
                "model_configured": True,
                "api_key_configured": True,
                "openai_api_key_configured": True,
                "openrouter_api_key_configured": False,
            },
            status,
        )
        self.assertNotIn("secret-test-key", str(status))

    def test_ollama_provider_uses_safe_local_defaults_without_key(self) -> None:
        config = server.llm_config_from_payload(
            {"provider": "ollama"}, timeout_seconds=30, temperature=0.1
        )

        self.assertEqual("qwen2.5-coder:1.5b", config.model)
        self.assertEqual("http://127.0.0.1:11434/v1", config.base_url)
        self.assertEqual("ollama", config.api_key)
        self.assertEqual(300, config.timeout_seconds)

        client = server.coding_llm_client({"provider": "ollama"}, temperature=0.15)
        self.assertEqual("qwen2.5-coder:3b", client.config.model)
        self.assertEqual(0.0, client.config.temperature)
        response_format = server.coding_response_format({"provider": "ollama"})
        self.assertEqual("json_schema", response_format["type"])
        self.assertIn("patch", response_format["json_schema"]["schema"]["required"])
        self.assertEqual(768, server.coding_max_tokens({"provider": "ollama"}))
        self.assertFalse(server.coding_uses_edits({"provider": "ollama"}))

    def test_ollama_coding_context_is_small_and_ignores_build_outputs(self) -> None:
        generated = self.project / "build-qt" / "CMakeCache.txt"
        generated.parent.mkdir()
        generated.write_text("generated\n" * 4000, encoding="utf-8")

        context = server.coding_context(
            self.project,
            "修改 value 返回值",
            {"provider": "ollama"},
        )

        self.assertLessEqual(context["context_bytes"], server.OLLAMA_CODING_CONTEXT_BYTES)
        self.assertLessEqual(len(context["files"]), server.OLLAMA_CODING_MAX_FILES)
        self.assertNotIn("build-qt/CMakeCache.txt", [item["path"] for item in context["files"]])

    def test_ollama_status_reports_installed_models(self) -> None:
        response = mock.MagicMock()
        response.__enter__.return_value.read.return_value = json.dumps(
            {
                "models": [
                    {"name": "qwen2.5-coder:1.5b"},
                    {"name": "qwen2.5-coder:3b"},
                    {"name": "other:latest"},
                ]
            }
        ).encode("utf-8")
        with mock.patch.object(server.urllib.request, "urlopen", return_value=response):
            status = server.ollama_status()

        self.assertTrue(status["available"])
        self.assertTrue(status["default_model_installed"])
        self.assertTrue(status["default_coding_model_installed"])
        self.assertEqual(
            ["other:latest", "qwen2.5-coder:1.5b", "qwen2.5-coder:3b"], status["models"]
        )

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
    def test_ollama_retries_malformed_diff_once(
        self, chat: mock.Mock, _analysis: mock.Mock
    ) -> None:
        malformed = json.dumps(
            {
                "summary": "调整返回值",
                "plan": [],
                "risks": [],
                "tests": [],
                "patch": (
                    "diff --git a/src/value.cpp b/src/value.cpp\n"
                    "--- a/src/value.cpp\n+++ b/src/value.cpp\n"
                    "-int value() { return 1; }\n+int value() { return 2; }\n"
                ),
            },
            ensure_ascii=False,
        )
        valid = json.dumps(
            {
                "summary": "调整返回值",
                "plan": ["修改实现"],
                "risks": [],
                "tests": ["ctest --test-dir build"],
                "patch": (
                    "diff --git a/src/value.cpp b/src/value.cpp\n"
                    "--- a/src/value.cpp\n+++ b/src/value.cpp\n"
                    "@@ -1 +1 @@\n"
                    "-int value() { return 1; }\n+int value() { return 2; }\n"
                ),
            },
            ensure_ascii=False,
        )
        chat.side_effect = [malformed, valid]

        result = server.handle_coding(
            {
                "project_path": str(self.project),
                "task": "把 value 返回值改为 2",
                "provider": "ollama",
                "model": "qwen2.5-coder:3b",
            }
        )

        self.assertEqual("pending", result["proposal"]["status"])
        self.assertEqual(2, chat.call_count)
        correction_messages = chat.call_args_list[1].args[0]
        self.assertIn("未通过校验", correction_messages[-1]["content"])

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
