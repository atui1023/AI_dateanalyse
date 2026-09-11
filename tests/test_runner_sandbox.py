import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import main


ROOT = Path(__file__).resolve().parents[1]


def run_runner(code: str) -> dict:
    with tempfile.TemporaryDirectory() as temp_dir:
        code_path = Path(temp_dir) / "analysis.py"
        code_path.write_text(code, encoding="utf-8")
        proc = subprocess.run(
            [sys.executable, str(ROOT / "runner.py"), str(code_path), "[]"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="strict",
            cwd=ROOT,
            check=False,
        )
    marker = "===RUNNER_ERR===" if "===RUNNER_ERR===" in proc.stdout else "===RUNNER_OK==="
    return json.loads(proc.stdout.split(marker, 1)[1].strip())


class RunnerSandboxTest(unittest.TestCase):
    def test_allows_normal_analysis_code(self) -> None:
        payload = run_runner("result = pd.DataFrame({'value': [1, 2, 3]})\nprint(result['value'].sum())")
        self.assertNotIn("error", payload)
        self.assertIn("6", payload["stdout"])

    def test_blocks_operating_system_import(self) -> None:
        payload = run_runner("import os\nresult = None")
        self.assertIn("SandboxViolation", payload["error"])
        self.assertIn("不允许导入模块", payload["error"])

    def test_blocks_file_write(self) -> None:
        payload = run_runner("open('sandbox-test.txt', 'w').write('x')")
        self.assertIn("SandboxViolation", payload["error"])
        self.assertFalse((ROOT / "sandbox-test.txt").exists())

    def test_blocks_indirect_os_access(self) -> None:
        payload = run_runner("pd.io.common.os.remove('anything')")
        self.assertIn("SandboxViolation", payload["error"])

    def test_blocks_pandas_file_read(self) -> None:
        payload = run_runner("result = pd.read_csv('private.csv')")
        self.assertIn("SandboxViolation", payload["error"])

    def test_blocks_network_module(self) -> None:
        payload = run_runner("import socket\nresult = None")
        self.assertIn("SandboxViolation", payload["error"])

    def test_parent_stops_timed_out_code(self) -> None:
        with patch.dict(os.environ, {"ANALYSIS_TIMEOUT_SECONDS": "1"}):
            payload = main.run_analysis([], "while True:\n    pass")
        self.assertIn("执行超时", payload["error"])

    def test_parent_stops_memory_limited_code(self) -> None:
        with patch.dict(os.environ, {"ANALYSIS_TIMEOUT_SECONDS": "5", "ANALYSIS_MEMORY_LIMIT_MB": "1"}):
            payload = main.run_analysis([], "while True:\n    pass")
        self.assertIn("内存超限", payload["error"])


if __name__ == "__main__":
    unittest.main()
