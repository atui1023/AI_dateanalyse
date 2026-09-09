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
import os
import sys
import tempfile
import warnings


def _configure_utf8_stdio() -> None:
    """让 Windows 沙箱输出不受父进程 GBK 控制台编码影响。"""
    # Always override inherited console encoding.
    os.environ["PYTHONIOENCODING"] = "utf-8:replace"
    os.environ["PYTHONUTF8"] = "1"
    streams = {
        sys.stdout,
        sys.stderr,
        getattr(sys, "__stdout__", None),
        getattr(sys, "__stderr__", None),
    }
    for stream in streams:
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, OSError):
            pass


# Windows 默认控制台可能是 GBK；分析输出统一使用 UTF-8，支持中文和 emoji。
_configure_utf8_stdio()

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

# 全局压制警告：pandas/numpy/sklearn 等库的 FutureWarning/DeprecationWarning
# 既避免污染 stderr，也防止模型代码把警告设为 error 后误触发异常
warnings.filterwarnings("ignore")


def load_dataframe(path, ext):
    """读取 CSV / Excel 为 DataFrame，CSV 自动尝试 gbk 编码"""
    if ext == ".csv":
        try:
            return pd.read_csv(path)
        except UnicodeDecodeError:
            return pd.read_csv(path, encoding="gbk")
    return pd.read_excel(path)


def main():
    # 整个 main 包一层 try/except，保证任何阶段崩溃都输出 RUNNER_ERR 标记，
    # 避免上层只看到“未知错误”而无法定位
    # 用 BaseException：SystemExit/KeyboardInterrupt 等非 Exception 子类也兜住
    try:
        _main()
    except BaseException as e:
        print("===RUNNER_ERR===")
        print(json.dumps({
            "stdout": "",
            "error": f"{type(e).__name__}: {e}",
        }, ensure_ascii=False))


def _main():
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
    # 用 fd 级重定向彻底拦截模型代码对真实 stdout/stderr 的写入（包括 os.write、
    # sys.__stdout__.write()、C 扩展库的 printf/fprintf 等），避免污染 runner 的标记输出。
    real_stdout_fd = os.dup(1)
    real_stderr_fd = os.dup(2)
    tmp_fd, tmp_path = tempfile.mkstemp()
    os.close(tmp_fd)
    exec_error = None
    try:
        with open(tmp_path, "w", encoding="utf-8") as tmp_f:
            os.dup2(tmp_f.fileno(), 1)
            os.dup2(tmp_f.fileno(), 2)  # stderr 也重定向，拦截 C 库 fprintf 等
        try:
            with contextlib.redirect_stdout(buf), contextlib.redirect_stderr(buf):
                exec(compile(code, "<analysis>", "exec"), {"__builtins__": __builtins__}, env)
        except BaseException as e:
            # 模型代码可能写了 sys.exit()/exit()/quit()，触发 SystemExit（继承自 BaseException
            # 而非 Exception）。若不在此拦截，SystemExit 会穿透 main() 的 except Exception，
            # 被 Python 解释器接管后进程以退出码 0 静默退出，main.py 拿到「无任何输出」无法定位。
            exec_error = e
    finally:
        os.dup2(real_stdout_fd, 1)  # 恢复真实 stdout，保证后续标记输出到管道
        os.dup2(real_stderr_fd, 2)
        os.close(real_stdout_fd)
        os.close(real_stderr_fd)

    # 合并 Python 层（print）与 fd 层（os.write 等）的输出
    try:
        with open(tmp_path, "r", encoding="utf-8", errors="replace") as f:
            fd_output = f.read()
    except OSError:
        fd_output = ""
    try:
        os.remove(tmp_path)
    except OSError:
        pass
    combined = buf.getvalue() + fd_output

    if exec_error is not None:
        print("===RUNNER_ERR===")
        print(json.dumps({
            "stdout": combined,
            "error": f"{type(exec_error).__name__}: {exec_error}",
        }, ensure_ascii=False))
        return

    # 3. 收集 result 表格（最多回传 200 行）
    table = None
    result = env.get("result")
    if isinstance(result, pd.DataFrame):
        # 逐单元格转字符串，NaN 转为空串（避免 fillna("") 的 FutureWarning）
        rows = []
        for _, row in result.head(200).iterrows():
            rows.append(["" if pd.isna(v) else str(v) for v in row])
        table = {
            "columns": [str(c) for c in result.columns],
            "rows": rows,
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
    print(json.dumps({"stdout": combined, "table": table, "chart": chart}, ensure_ascii=False))


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
