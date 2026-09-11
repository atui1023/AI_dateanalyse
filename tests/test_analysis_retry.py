import unittest
from unittest.mock import patch

import main


class AnalysisRetryTest(unittest.TestCase):
    def test_retries_with_repaired_code_and_returns_final_code(self) -> None:
        results = [
            {"error": "代码执行出错：NameError: name 'sales' is not defined"},
            {"table": {"columns": ["地区"], "rows": [["华东"]]}},
        ]
        with patch.object(main, "run_analysis", side_effect=results) as run, \
                patch.object(main.llm, "repair_analysis_code", return_value="result = df1.head()") as repair:
            payload = main.run_analysis_with_retries([], "按地区分析", "result = sales")

        self.assertEqual(run.call_count, 2)
        repair.assert_called_once()
        self.assertEqual(payload["retry_count"], 1)
        self.assertEqual(payload["code"], "result = df1.head()")
        self.assertNotIn("error", payload)

    def test_stops_after_two_repairs(self) -> None:
        failure = {"error": "代码执行出错：SyntaxError"}
        with patch.object(main, "run_analysis", return_value=failure) as run, \
                patch.object(main.llm, "repair_analysis_code", side_effect=["code2", "code3"]) as repair:
            payload = main.run_analysis_with_retries([], "分析", "code1")

        self.assertEqual(run.call_count, 3)
        self.assertEqual(repair.call_count, 2)
        self.assertEqual(payload["retry_count"], 2)
        self.assertEqual(payload["code"], "code3")
        self.assertIn("error", payload)


if __name__ == "__main__":
    unittest.main()
