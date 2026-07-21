from __future__ import annotations

import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from agent.project_agent.coding_agent import (
    CodingAgentError,
    apply_proposal,
    collect_code_context,
    load_proposal,
    parse_coding_edits,
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

    def test_concurrent_apply_only_mutates_project_once(self) -> None:
        proposal = self.make_proposal()

        def attempt_apply() -> str:
            try:
                apply_proposal(self.proposal_dir, self.backup_dir, str(proposal["id"]))
                return "applied"
            except CodingAgentError:
                return "rejected"

        with ThreadPoolExecutor(max_workers=2) as executor:
            outcomes = list(executor.map(lambda _index: attempt_apply(), range(2)))

        self.assertEqual(["applied", "rejected"], sorted(outcomes))
        self.assertIn("return 2", self.source.read_text(encoding="utf-8"))
        stored = load_proposal(self.proposal_dir, str(proposal["id"]))
        self.assertEqual("applied", stored["status"])

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

    def test_context_ignores_prefixed_build_directories(self) -> None:
        generated = self.project / "build-qt" / "CMakeCache.txt"
        generated.parent.mkdir()
        generated.write_text("generated cache\n" * 2000, encoding="utf-8")

        context = collect_code_context(self.project, "修改 value", max_context_bytes=4096)
        paths = [item["path"] for item in context["files"]]

        self.assertNotIn("build-qt/CMakeCache.txt", paths)
        self.assertLessEqual(context["context_bytes"], 4096)

    def test_context_respects_local_model_byte_limit(self) -> None:
        self.source.write_text("int value = 1;\n" * 1000, encoding="utf-8")

        context = collect_code_context(
            self.project,
            "修改 value",
            max_files=2,
            max_context_bytes=2048,
        )

        self.assertLessEqual(context["context_bytes"], 2048)

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

    def test_parse_coding_response_adds_missing_git_diff_header(self) -> None:
        response = (
            '{"summary":"修改", "plan":[], "risks":[], "tests":[], '
            '"patch":"--- a/src/value.cpp\\n+++ b/src/value.cpp\\n'
            '@@ -1 +1 @@\\n-int value() { return 1; }\\n'
            '+int value() { return 2; }\\n"}'
        )

        parsed = parse_coding_response(response)

        self.assertTrue(parsed["patch"].startswith("diff --git a/src/value.cpp b/src/value.cpp\n"))
        self.assertEqual(["src/value.cpp"], validate_patch(self.project, parsed["patch"]))

    def test_parse_coding_response_adds_missing_context_prefix(self) -> None:
        response = (
            '{"summary":"修改", "plan":[], "risks":[], "tests":[], '
            '"patch":"--- a/src/value.cpp\\n+++ b/src/value.cpp\\n'
            '@@ -1 +1,2 @@\\nint value() { return 1; }\\n'
            '+int other() { return 2; }\\n"}'
        )

        parsed = parse_coding_response(response)

        self.assertIn("\n int value() { return 1; }\n", parsed["patch"])
        self.assertEqual(["src/value.cpp"], validate_patch(self.project, parsed["patch"]))

    def test_parse_coding_response_recovers_missing_addition_prefix_from_source(self) -> None:
        response = (
            '{"summary":"新增函数", "plan":[], "risks":[], "tests":[], '
            '"patch":"--- a/src/value.cpp\\n+++ b/src/value.cpp\\n'
            '@@ -1 +1,2 @@\\nint value() { return 1; }\\n'
            'int other() { return 2; }\\n"}'
        )

        parsed = parse_coding_response(response, self.project)

        self.assertIn("\n int value() { return 1; }\n", parsed["patch"])
        self.assertIn("\n+int other() { return 2; }\n", parsed["patch"])
        self.assertEqual(["src/value.cpp"], validate_patch(self.project, parsed["patch"]))

    def test_parse_coding_response_recovers_unprefixed_indented_lines(self) -> None:
        self.source.write_text(
            "int value() {\n    return 1;\n}\n",
            encoding="utf-8",
        )
        response = (
            '{"summary":"新增局部变量", "plan":[], "risks":[], "tests":[], '
            '"patch":"--- a/src/value.cpp\\n+++ b/src/value.cpp\\n'
            '@@ -1,3 +1,4 @@\\nint value() {\\n'
            '    int other = 2;\\n    return 1;\\n}\\n"}'
        )

        parsed = parse_coding_response(response, self.project)

        self.assertIn("\n+    int other = 2;\n", parsed["patch"])
        self.assertIn("\n     return 1;\n", parsed["patch"])
        self.assertEqual(["src/value.cpp"], validate_patch(self.project, parsed["patch"]))

    def test_parse_coding_response_removes_stray_diff_preamble_path(self) -> None:
        response = json.dumps(
            {
                "summary": "调整返回值",
                "plan": [],
                "risks": [],
                "tests": [],
                "patch": (
                    "diff --git a/src/value.cpp b/src/value.cpp\n"
                    "index 1234567..89abcdef 100644\n"
                    "src/values.cpp\n"
                    "--- a/src/value.cpp\n"
                    "+++ b/src/value.cpp\n"
                    "@@ -1 +1 @@\n"
                    "-int value() { return 1; }\n"
                    "+int value() { return 2; }\n"
                ),
            },
            ensure_ascii=False,
        )

        parsed = parse_coding_response(response, self.project)

        self.assertNotIn("src/values.cpp", parsed["patch"])
        self.assertEqual(["src/value.cpp"], validate_patch(self.project, parsed["patch"]))

    def test_parse_coding_response_repairs_real_local_model_diff(self) -> None:
        target = self.project / "tests" / "value_test.cpp"
        target.parent.mkdir()
        target.write_text(
            "#include <cassert>\n\n"
            "int main() {\n"
            "    assert(1 + 1 == 2);\n"
            "    return 0;\n"
            "}\n",
            encoding="utf-8",
        )
        response = json.dumps(
            {
                "summary": "添加断言",
                "plan": [],
                "risks": [],
                "tests": ["value_test"],
                "patch": (
                    "diff --git a/tests/value_test.cpp b/tests/value_test.cpp\n"
                    "index 1234567..89abcdef 100644\n"
                    "test/value_tests.cpp\n"
                    "--- a/tests/value_test.cpp\n"
                    "+++ b/tests/value_test.cpp\n"
                    "@@ -1,4 +1,5 @@\n"
                    "#include <cassert>\n\n"
                    "int main() {\n"
                    "    assert(1 + 1 == 2);\n"
                    "+    assert(2 + 2 == 4);\n"
                    "    return 0;\n"
                    "}\n"
                ),
            },
            ensure_ascii=False,
        )

        parsed = parse_coding_response(response, self.project)

        self.assertNotIn("test/value_tests.cpp", parsed["patch"])
        self.assertEqual(["tests/value_test.cpp"], validate_patch(self.project, parsed["patch"]))

    def test_parse_coding_edits_generates_valid_diff(self) -> None:
        response = json.dumps(
            {
                "summary": "新增函数",
                "plan": ["修改实现"],
                "risks": [],
                "tests": ["运行测试"],
                "edits": [
                    {
                        "path": "src/value.cpp",
                        "start_line": 1,
                        "end_line": 1,
                        "replacement": (
                            "int value() { return 1; }\n"
                            "int other() { return 2; }"
                        ),
                    }
                ],
            },
            ensure_ascii=False,
        )

        parsed = parse_coding_edits(response, self.project)

        self.assertIn("+int other() { return 2; }", parsed["patch"])
        self.assertEqual(["src/value.cpp"], validate_patch(self.project, parsed["patch"]))

    def test_parse_coding_edits_allows_append_after_last_line(self) -> None:
        response = json.dumps(
            {
                "summary": "追加函数",
                "plan": [],
                "risks": [],
                "tests": [],
                "edits": [
                    {
                        "path": "src/value.cpp",
                        "start_line": 2,
                        "end_line": 2,
                        "replacement": "int other() { return 2; }",
                    }
                ],
            },
            ensure_ascii=False,
        )

        parsed = parse_coding_edits(response, self.project)

        self.assertIn("+int other() { return 2; }", parsed["patch"])
        self.assertEqual(["src/value.cpp"], validate_patch(self.project, parsed["patch"]))

    def test_parse_coding_edits_normalizes_virtual_append_position(self) -> None:
        response = json.dumps(
            {
                "summary": "追加函数",
                "plan": [],
                "risks": [],
                "tests": [],
                "edits": [
                    {
                        "path": "src/value.cpp",
                        "start_line": 10,
                        "end_line": 10,
                        "replacement": "int other() { return 2; }",
                    }
                ],
            },
            ensure_ascii=False,
        )

        parsed = parse_coding_edits(response, self.project)

        self.assertIn("+int other() { return 2; }", parsed["patch"])
        self.assertEqual(["src/value.cpp"], validate_patch(self.project, parsed["patch"]))

    def test_parse_coding_edits_normalizes_virtual_append_range(self) -> None:
        response = json.dumps(
            {
                "summary": "追加函数",
                "plan": [],
                "risks": [],
                "tests": [],
                "edits": [
                    {
                        "path": "src/value.cpp",
                        "start_line": 6,
                        "end_line": 10,
                        "replacement": "int other() { return 2; }",
                    }
                ],
            },
            ensure_ascii=False,
        )

        parsed = parse_coding_edits(response, self.project)

        self.assertIn("+int other() { return 2; }", parsed["patch"])
        self.assertEqual(["src/value.cpp"], validate_patch(self.project, parsed["patch"]))

    def test_parse_coding_edits_clamps_range_to_end_of_file(self) -> None:
        response = json.dumps(
            {
                "summary": "修改文件末尾",
                "plan": [],
                "risks": [],
                "tests": [],
                "edits": [
                    {
                        "path": "src/value.cpp",
                        "start_line": 1,
                        "end_line": 2,
                        "replacement": (
                            "int value() { return 1; }\n"
                            "int other() { return 2; }"
                        ),
                    }
                ],
            },
            ensure_ascii=False,
        )

        parsed = parse_coding_edits(response, self.project)

        self.assertIn("+int other() { return 2; }", parsed["patch"])
        self.assertEqual(["src/value.cpp"], validate_patch(self.project, parsed["patch"]))

    def test_parse_coding_edits_resolves_unique_misplaced_path(self) -> None:
        header = self.project / "include" / "value.h"
        header.parent.mkdir()
        header.write_text("int value();\n", encoding="utf-8")
        response = json.dumps(
            {
                "summary": "追加声明",
                "plan": [],
                "risks": [],
                "tests": [],
                "edits": [
                    {
                        "path": "src/value.h",
                        "start_line": 2,
                        "end_line": 2,
                        "replacement": "int other();",
                    }
                ],
            },
            ensure_ascii=False,
        )

        parsed = parse_coding_edits(response, self.project)

        self.assertIn("diff --git a/include/value.h b/include/value.h", parsed["patch"])
        self.assertEqual(["include/value.h"], validate_patch(self.project, parsed["patch"]))


if __name__ == "__main__":
    unittest.main()
