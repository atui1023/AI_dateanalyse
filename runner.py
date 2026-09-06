"""沙箱执行器：在独立子进程中运行模型生成的 pandas 分析代码。

用法：python runner.py <代码文件路径> <manifest JSON>

manifest JSON 格式：[{"path": 数据文件路径, "ext": 扩展名}, ...]

约定：
- 各数据集按顺序加载为 DataFrame 变量 df1、df2、……（df 等价于 df1），pd / np 可用
- 预装库：scipy、sklearn、statsmodels（顶层已导入，子模块可自行 import）
- 模型把最终表格结果赋值给 result（DataFrame）
- 模型可赋值 chart（dict）作为 ECharts option，用于前端图表渲染
- print() 输出的内容会被收集为文本结论
- 执行结束后打印 ===RUNNER_OK=== + JSON 结果；出错打印 ===RUNNER_ERR=== + JSON
"""
import contextlib
import io
import json
import sys

import numpy as np
import pandas as pd

# 预导入常用数据分析/机器学习库到沙箱顶层，模型也可自行 import 子模块
try:
    import scipy
except ImportError:
    scipy = None
try:
    import sklearn
except ImportError:
    sklearn = None
try:
    import statsmodels
except ImportError:
    statsmodels = None


def load_dataframe(path, ext):
    """读取 CSV / Excel 为 DataFrame，CSV 自动尝试 gbk 编码"""
    if ext == ".csv":
        try:
            return pd.read_csv(path)
        except UnicodeDecodeError:
            return pd.read_csv(path, encoding="gbk")
    return pd.read_excel(path)


def main():
    code_path = sys.argv[1]
    manifest = json.loads(sys.argv[2])

    # 1. 按顺序加载所有数据集：df1、df2、……
    env = {"pd": pd, "np": np, "scipy": scipy, "sklearn": sklearn, "statsmodels": statsmodels}
    for i, item in enumerate(manifest, start=1):
        env[f"df{i}"] = load_dataframe(item["path"], item["ext"])
    if manifest:
        env["df"] = env["df1"]  # 单文件场景下的便捷别名

    # 2. 读取并执行模型生成的代码
    with open(code_path, "r", encoding="utf-8") as f:
        code = f.read()

    buf = io.StringIO()
    try:
        with contextlib.redirect_stdout(buf):
            exec(compile(code, "<analysis>", "exec"), {"__builtins__": __builtins__}, env)
    except Exception as e:
        print("===RUNNER_ERR===")
        print(json.dumps({
            "stdout": buf.getvalue(),
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))
        return

    # 3. 收集 result 表格（最多回传 200 行）
    table = None
    result = env.get("result")
    if isinstance(result, pd.DataFrame):
        table = {
            "columns": [str(c) for c in result.columns],
            "rows": result.head(200).fillna("").astype(str).values.tolist(),
            "truncated": int(result.shape[0]) > 200,
        }

    # 4. 收集 chart（ECharts option 字典），numpy 类型转成原生类型以便 JSON 序列化
    chart = None
    chart_env = env.get("chart")
    if isinstance(chart_env, dict):
        try:
            chart = json.loads(json.dumps(chart_env, default=_json_default))
        except (TypeError, ValueError):
            chart = None

    print("===RUNNER_OK===")
    print(json.dumps({"stdout": buf.getvalue(), "table": table, "chart": chart}, ensure_ascii=False))


def _json_default(o):
    if isinstance(o, np.integer):
        return int(o)
    if isinstance(o, np.floating):
        return float(o)
    if isinstance(o, np.ndarray):
        return o.tolist()
    if isinstance(o, (pd.Timestamp,)):
        return str(o)
    return str(o)


if __name__ == "__main__":
    main()
