import unittest

import anomaly_rules


class AnomalyRulesTest(unittest.TestCase):
    def test_threshold_required_and_change_rules(self):
        alerts = anomaly_rules.evaluate(
            {"columns": ["amount", "region"], "rows": [[10, "East"], [30, "West"]]},
            {"thresholds": [{"column": "amount", "max": 20}], "required_columns": ["date"], "change_rate": {"column": "amount", "limit": 50}},
        )
        self.assertEqual({item["type"] for item in alerts}, {"threshold", "missing_column", "change_rate"})


if __name__ == "__main__":
    unittest.main()
