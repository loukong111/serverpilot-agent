from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

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
        self.report_dir = self.root / "reports"
        self.history_dir = self.report_dir / "history"
        self.report_patch = mock.patch.object(server, "REPORT_DIR", self.report_dir)
        self.history_patch = mock.patch.object(server, "HISTORY_DIR", self.history_dir)
        self.report_patch.start()
        self.history_patch.start()

    def tearDown(self) -> None:
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


if __name__ == "__main__":
    unittest.main()
