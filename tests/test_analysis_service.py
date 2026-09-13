import asyncio
import os
import tempfile
import unittest
from unittest.mock import patch

import main
import kb


class FakeUpload:
    def __init__(self, chunks):
        self.chunks = list(chunks)

    async def read(self, _size):
        return self.chunks.pop(0) if self.chunks else b""


class AnalysisServiceTest(unittest.TestCase):
    def test_generated_utf8_csv_can_be_read_by_knowledge_base(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "关联结果.csv")
            with open(path, "w", encoding="utf-8-sig", newline="") as handle:
                handle.write("地区,销售额\n华东,100\n")
            pages = kb._tabular_to_pages(path, ".csv")
            self.assertTrue(pages)
            self.assertIn("华东", pages[0])

    def test_instruction_service_runs_code_and_keeps_metadata(self):
        datasets = [{"doc_id": "doc-1", "filename": "sales.csv", "path": "sales.csv", "ext": ".csv", "summary": {"rows": 1, "cols": 1, "columns": [{"name": "amount", "dtype": "int64", "samples": ["1"]}], "preview_columns": ["amount"], "preview_rows": [], "quality": {}}}]
        response = type("Response", (), {"content": "```python result = df1.head() ```"})()
        result = {"table": {"columns": ["amount"], "rows": [[1]]}, "chart": None, "stdout": "ok", "error": None, "retry_count": 0, "code": "result = df1.head()"}
        with patch.object(type(main.llm.chat_model), "invoke", return_value=response), patch.object(main, "run_analysis_with_retries", return_value=result):
            payload = main.execute_analysis_instruction(datasets, "查看金额")
        self.assertEqual(payload["datasets"][0]["dataset_id"], "doc-1")
        self.assertEqual(payload["code"], "result = df1.head()")
        self.assertIn("execution_ms", payload)

    def test_upload_limit_removes_partial_file(self):
        with tempfile.TemporaryDirectory() as directory:
            target = os.path.join(directory, "too-large.csv")
            with self.assertRaises(main.HTTPException):
                asyncio.run(main.save_upload_limited(FakeUpload([b"1234", b"5678"]), target, 5))
            self.assertFalse(os.path.exists(target))


if __name__ == "__main__":
    unittest.main()
