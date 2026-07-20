from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from agent.project_agent.coding_agent import (
    CodingAgentError,
    apply_proposal,
    collect_code_context,
    parse_coding_response,
    rollback_proposal,
    save_proposal,
    validate_patch,
)


class CodingAgentTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_dir.name)
        self.project = self.root / "sample"
        self.source = self.project / "src" / "value.cpp"
        self.source.parent.mkdir(parents=True)
        self.source.write_text("int value() { return 1; }\n", encoding="utf-8")
        (self.project / "CMakeLists.txt").write_text(
            "cmake_minimum_required(VERSION 3.16)\nproject(sample)\n",
            encoding="utf-8",
        )
        self.proposal_dir = self.root / "proposals"
        self.backup_dir = self.root / "backups"

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    @staticmethod
    def patch() -> str:
        return (
            "diff --git a/src/value.cpp b/src/value.cpp\n"
            "--- a/src/value.cpp\n"
            "+++ b/src/value.cpp\n"
            "@@ -1 +1 @@\n"
            "-int value() { return 1; }\n"
            "+int value() { return 2; }\n"
        )

    def make_proposal(self) -> dict[str, object]:
        proposal = {
            "summary": "调整返回值",
            "plan": ["修改实现", "运行测试"],
            "risks": [],
            "tests": ["cmake --build build"],
            "patch": self.patch(),
        }
        context = collect_code_context(self.project, "修改 value 返回值")
        validate_patch(self.project, self.patch())
        return save_proposal(self.proposal_dir, self.project, "修改 value 返回值", proposal, context)

    def test_apply_and_rollback_proposal(self) -> None:
        proposal = self.make_proposal()

        applied = apply_proposal(self.proposal_dir, self.backup_dir, str(proposal["id"]))
        self.assertEqual("applied", applied["status"])
        self.assertIn("return 2", self.source.read_text(encoding="utf-8"))

        rolled_back = rollback_proposal(self.proposal_dir, str(proposal["id"]))
        self.assertEqual("rolled_back", rolled_back["status"])
        self.assertIn("return 1", self.source.read_text(encoding="utf-8"))

    def test_rollback_refuses_to_overwrite_later_changes(self) -> None:
        proposal = self.make_proposal()
        apply_proposal(self.proposal_dir, self.backup_dir, str(proposal["id"]))
        self.source.write_text("int value() { return 3; }\n", encoding="utf-8")

        with self.assertRaisesRegex(CodingAgentError, "又被修改"):
            rollback_proposal(self.proposal_dir, str(proposal["id"]))

    def test_new_file_is_removed_by_rollback(self) -> None:
        patch = (
            "diff --git a/tests/value_test.cpp b/tests/value_test.cpp\n"
            "new file mode 100644\n"
            "--- /dev/null\n"
            "+++ b/tests/value_test.cpp\n"
            "@@ -0,0 +1 @@\n"
            "+static_assert(1 + 1 == 2);\n"
        )
        proposal_data = {
            "summary": "添加测试",
            "plan": ["新增测试文件"],
            "risks": [],
            "tests": [],
            "patch": patch,
        }
        validate_patch(self.project, patch)
        proposal = save_proposal(
            self.proposal_dir,
            self.project,
            "添加测试",
            proposal_data,
            collect_code_context(self.project, "value test"),
        )

        apply_proposal(self.proposal_dir, self.backup_dir, str(proposal["id"]))
        new_file = self.project / "tests" / "value_test.cpp"
        self.assertTrue(new_file.is_file())

        rollback_proposal(self.proposal_dir, str(proposal["id"]))
        self.assertFalse(new_file.exists())

    def test_validate_patch_rejects_unsafe_and_generated_paths(self) -> None:
        unsafe = (
            "diff --git a/../outside.cpp b/../outside.cpp\n"
            "--- a/../outside.cpp\n+++ b/../outside.cpp\n"
            "@@ -1 +1 @@\n-old\n+new\n"
        )
        generated = (
            "diff --git a/build/generated.cpp b/build/generated.cpp\n"
            "--- a/build/generated.cpp\n+++ b/build/generated.cpp\n"
            "@@ -1 +1 @@\n-old\n+new\n"
        )

        with self.assertRaises(CodingAgentError):
            validate_patch(self.project, unsafe)
        with self.assertRaisesRegex(CodingAgentError, "不允许修改"):
            validate_patch(self.project, generated)

    def test_validate_patch_rejects_file_deletion(self) -> None:
        deletion = (
            "diff --git a/src/value.cpp b/src/value.cpp\n"
            "deleted file mode 100644\n"
            "--- a/src/value.cpp\n+++ /dev/null\n"
            "@@ -1 +0,0 @@\n-int value() { return 1; }\n"
        )
        with self.assertRaisesRegex(CodingAgentError, "不支持删除"):
            validate_patch(self.project, deletion)

    def test_parse_coding_response_accepts_json_code_fence(self) -> None:
        response = (
            "```json\n"
            '{"summary":"修改", "plan":[], "risks":[], "tests":[], '
            '"patch":"diff --git a/src/value.cpp b/src/value.cpp\\n@@ -1 +1 @@\\n-a\\n+b\\n"}'
            "\n```"
        )
        parsed = parse_coding_response(response)
        self.assertEqual("修改", parsed["summary"])
        self.assertTrue(parsed["patch"].endswith("\n"))


if __name__ == "__main__":
    unittest.main()
