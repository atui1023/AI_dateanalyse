"""运行 P0/P1 综合验收测试。

用法：
    .venv\Scripts\python.exe scripts\run_acceptance_tests.py

测试使用远程 embedding 配置占位值，避免验收时触发本地模型下载；不会修改业务数据库。
"""
from __future__ import annotations

import os
import sys
import unittest
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
REPORT = ROOT / "acceptance-report.txt"
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def main() -> int:
    os.environ.setdefault("EMBEDDING_PROVIDER", "remote")
    os.environ.setdefault("API_KEY", "acceptance-test-key")
    os.environ.setdefault("BASE_URL", "http://127.0.0.1:9/v1")
    suite = unittest.defaultTestLoader.discover(str(ROOT / "tests"))
    with REPORT.open("w", encoding="utf-8") as handle:
        handle.write(f"AI Data Analysis acceptance report\nGenerated: {datetime.now().isoformat(timespec='seconds')}\n\n")
        runner = unittest.TextTestRunner(stream=handle, verbosity=2)
        result = runner.run(suite)
        handle.write("\nSummary\n")
        handle.write(f"tests={result.testsRun}\nfailures={len(result.failures)}\nerrors={len(result.errors)}\n")
        handle.write("status=" + ("PASS" if result.wasSuccessful() else "FAIL") + "\n")
    print(f"Acceptance tests: {'PASS' if result.wasSuccessful() else 'FAIL'}")
    print(f"Tests: {result.testsRun}, failures: {len(result.failures)}, errors: {len(result.errors)}")
    print(f"Report: {REPORT}")
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    sys.exit(main())
