import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class RunnerEncodingTest(unittest.TestCase):
    def test_runner_handles_non_gbk_output(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            code_path = Path(temp_dir) / "analysis.py"
            code_path.write_text(
                "result = None\nprint('analysis complete ✓')\n",
                encoding="utf-8",
            )

            proc = subprocess.run(
                [sys.executable, str(ROOT / "runner.py"), str(code_path), "[]"],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
                env={**os.environ, "PYTHONIOENCODING": "gbk"},
                check=False,
                cwd=ROOT,
            )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("===RUNNER_OK===", proc.stdout)
        payload = proc.stdout.split("===RUNNER_OK===", 1)[1].strip()
        self.assertIn("analysis complete ✓", json.loads(payload)["stdout"])


if __name__ == "__main__":
    unittest.main()
