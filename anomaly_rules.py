"""轻量异常规则引擎：阈值、数据缺失和相邻周期变化。"""
from __future__ import annotations

from typing import Any


def evaluate(table: dict[str, Any], rules: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    rules = rules or {}
    columns = list(table.get("columns") or [])
    rows = list(table.get("rows") or [])
    alerts: list[dict[str, Any]] = []
    thresholds = rules.get("thresholds") or []
    for rule in thresholds:
        column = str(rule.get("column") or "")
        if column not in columns:
            continue
        index = columns.index(column)
        minimum = rule.get("min")
        maximum = rule.get("max")
        for row_index, row in enumerate(rows):
            try:
                value = float(row[index])
            except (IndexError, TypeError, ValueError):
                continue
            if minimum is not None and value < float(minimum):
                alerts.append({"type": "threshold", "column": column, "row": row_index, "value": value, "message": f"{column} 低于阈值 {minimum}"})
            if maximum is not None and value > float(maximum):
                alerts.append({"type": "threshold", "column": column, "row": row_index, "value": value, "message": f"{column} 高于阈值 {maximum}"})
    for column in rules.get("required_columns") or []:
        if column not in columns:
            alerts.append({"type": "missing_column", "column": column, "message": f"缺少必需字段：{column}"})
    change = rules.get("change_rate") or {}
    column = str(change.get("column") or "")
    if column in columns and len(rows) >= 2:
        index = columns.index(column)
        try:
            previous, latest = float(rows[-2][index]), float(rows[-1][index])
            if previous != 0:
                rate = (latest - previous) / abs(previous) * 100
                limit = float(change.get("limit", 20))
                if abs(rate) >= limit:
                    alerts.append({"type": "change_rate", "column": column, "value": rate, "message": f"{column} 相邻周期变化 {rate:.2f}%"})
        except (IndexError, TypeError, ValueError):
            pass
    return alerts
