from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest import mock

from agent.project_agent import diagnostics


class DiagnosticFlowTest(unittest.TestCase):
    def test_build_and_test_are_skipped_after_configure_failure(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            failed = diagnostics.DiagnosticStep(
                name="configure",
                success=False,
                exit_code=1,
                observation="command failed",
            )
            with mock.patch.object(diagnostics, "run_command_step", return_value=failed) as run_step:
                result = diagnostics.run_diagnostics(
                    project_path=root,
                    build_dir=root / "build",
                    cmake_args=[],
                    build_args=[],
                    ctest_args=[],
                    skip_configure=False,
                    skip_build=False,
                    skip_test=False,
                    start_command=None,
                    startup_seconds=0.1,
                    benchmark_command=None,
                    stats_url=None,
                    timeout_seconds=5,
                )

        self.assertEqual(run_step.call_count, 1)
        self.assertEqual([step.name for step in result.steps], ["configure", "build", "test"])
        self.assertTrue(result.steps[1].skipped)
        self.assertTrue(result.steps[2].skipped)


if __name__ == "__main__":
    unittest.main()
