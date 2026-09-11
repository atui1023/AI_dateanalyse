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


    def test_runner_exposes_dataframe_to_nested_code(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            temp = Path(temp_dir)
            data_path = temp / "data.csv"
            data_path.write_text("地区,销售额\n华东,100\n华南,200\n", encoding="utf-8")
            code_path = temp / "analysis.py"
            code_path.write_text(
                "def build_result():\n    return df1.groupby('地区')['销售额'].sum().reset_index()\n"
                "result = build_result()\n",
                encoding="utf-8",
            )
            manifest = json.dumps([{"path": str(data_path), "ext": ".csv"}], ensure_ascii=False)
            proc = subprocess.run(
                [sys.executable, str(ROOT / "runner.py"), str(code_path), manifest],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="strict",
                check=False,
                cwd=ROOT,
            )

        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIn("===RUNNER_OK===", proc.stdout)
        payload = proc.stdout.split("===RUNNER_OK===", 1)[1].strip()
        parsed = json.loads(payload)
        self.assertIsNone(parsed.get("error"))
        self.assertEqual(parsed["table"]["columns"], ["地区", "销售额"])
        self.assertEqual(len(parsed["table"]["rows"]), 2)
if __name__ == "__main__":
    unittest.main()
