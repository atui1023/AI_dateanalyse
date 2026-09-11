"""沙箱执行器：在独立子进程中运行模型生成的 pandas 分析代码。

用法：python runner.py <代码文件路径> <manifest JSON>

manifest JSON 格式：[{"path": 数据文件路径, "ext": 扩展名}, ...]

约定：
- 各数据集按顺序加载为 DataFrame 变量 df1、df2、……（df 等价于 df1），pd / np 可用
- 预装库：scipy、sklearn、statsmodels（顶层已导入，子模块可自行 import）
- 基础库：pandas、numpy、openpyxl
- 模型把最终表格结果赋值给 result（DataFrame）
- 模型可赋值 chart（dict）作为 ECharts option，用于前端图表渲染
- print() 输出的内容会被收集为文本结论
- 执行结束后打印 ===RUNNER_OK=== + JSON 结果；出错打印 ===RUNNER_ERR=== + JSON
"""
import contextlib
import ast
import builtins
import io
import json
import os
import sys
import tempfile
import warnings


ALLOWED_IMPORTS = {
    "collections", "datetime", "decimal", "functools", "itertools", "json",
    "math", "numpy", "pandas", "scipy", "sklearn", "statistics", "statsmodels",
}
FORBIDDEN_NAMES = {
    "ctypes", "ftplib", "http", "os", "pathlib", "requests", "shutil", "socket",
    "subprocess", "sys", "urllib",
}
FORBIDDEN_CALLS = {
    "breakpoint", "compile", "delattr", "dir", "eval", "exec", "exit", "getattr",
    "globals", "help", "input", "locals", "open", "quit", "setattr", "vars",
}
FORBIDDEN_ATTRIBUTES = {
    "ExcelFile", "HDFStore", "Popen", "call", "chmod", "chown", "connect", "ctypes",
    "fork", "fromfile", "genfromtxt", "kill", "load",
    "loadtxt", "memmap", "mkdir", "open", "popen", "read_clipboard", "read_csv",
    "read_excel", "read_feather", "read_fwf", "read_hdf", "read_html", "read_json",
    "read_orc", "read_parquet", "read_pickle", "read_sas", "read_spss", "read_sql",
    "read_sql_query", "read_sql_table", "read_stata", "read_table", "read_xml", "remove", "rename",
    "replace", "request", "rmdir", "save", "send", "spawn", "system", "touch",
    "to_clipboard", "to_csv", "to_excel", "to_feather", "to_json", "to_parquet",
    "to_pickle", "unlink", "urlopen", "write", "writelines", *FORBIDDEN_NAMES,
}
FORBIDDEN_CALL_PREFIXES = ("fetch_", "download_")
MAX_CAPTURE_CHARS = 1_000_000


class SandboxViolation(ValueError):
    pass


class LimitedStringIO(io.StringIO):
    """Limit model output retained in memory while keeping print semantics."""

    def __init__(self, limit: int = MAX_CAPTURE_CHARS):
        super().__init__()
        self.limit = limit
        self.truncated = False

    def write(self, value: str) -> int:
        remaining = self.limit - self.tell()
        if remaining <= 0:
            self.truncated = True
            return len(value)
        if len(value) > remaining:
            super().write(value[:remaining])
            self.truncated = True
            return len(value)
        return super().write(value)


def validate_analysis_code(code: str) -> None:
    """Reject code that can escape the read-only analysis environment."""
    try:
        tree = ast.parse(code, filename="<analysis>", mode="exec")
    except SyntaxError:
        raise
    for node in ast.walk(tree):
        if isinstance(node, (ast.Import, ast.ImportFrom)):
            modules = [alias.name for alias in node.names] if isinstance(node, ast.Import) else [node.module or ""]
            for module in modules:
                root = module.split(".", 1)[0]
                if root not in ALLOWED_IMPORTS:
                    raise SandboxViolation(f"不允许导入模块：{root or module}")
        if isinstance(node, ast.Name):
            if node.id in FORBIDDEN_NAMES or node.id.startswith("__"):
                raise SandboxViolation(f"不允许访问名称：{node.id}")
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("__") or node.attr in FORBIDDEN_ATTRIBUTES:
                raise SandboxViolation(f"不允许访问属性：{node.attr}")
        if isinstance(node, ast.Call):
            call_name = ""
            if isinstance(node.func, ast.Name):
                call_name = node.func.id
            elif isinstance(node.func, ast.Attribute):
                call_name = node.func.attr
            if call_name in FORBIDDEN_CALLS or call_name.startswith(FORBIDDEN_CALL_PREFIXES):
                raise SandboxViolation(f"不允许调用函数：{call_name}")


def safe_import(name, globals=None, locals=None, fromlist=(), level=0):
    root = name.split(".", 1)[0]
    if level or root not in ALLOWED_IMPORTS:
        raise SandboxViolation(f"不允许导入模块：{name}")
    return builtins.__import__(name, globals, locals, fromlist, level)


def safe_builtins() -> dict:
    allowed = {
        "Exception", "KeyError", "RuntimeError", "TypeError", "ValueError", "ZeroDivisionError",
        "abs", "all", "any", "bool", "dict", "enumerate", "filter", "float", "format",
        "int", "isinstance", "len", "list", "map", "max", "min", "next", "object",
        "pow", "print", "range", "reversed", "round", "set", "slice", "sorted", "str",
        "sum", "tuple", "type", "zip",
    }
    env = {name: getattr(builtins, name) for name in allowed}
    env["__import__"] = safe_import
    return env


def apply_resource_limits() -> None:
    """Apply hard CPU/address-space limits where the platform supports resource."""
    try:
        import resource
    except ImportError:
        return
    resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    memory_limit = 1536 * 1024 * 1024
    resource.setrlimit(resource.RLIMIT_AS, (memory_limit, memory_limit))


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
# 这些依赖由 requirements.txt 固定，缺失时保留兼容值，启动阶段不会静默崩溃
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
    apply_resource_limits()
    code_path = sys.argv[1]
    manifest = json.loads(sys.argv[2])

    # 1. 按顺序加载所有数据集：df1、df2、……
    env = {"pd": pd, "np": np, "scipy": scipy, "sklearn": sklearn, "statsmodels": statsmodels,
           "__name__": "__analysis__"}
    for i, item in enumerate(manifest, start=1):
        env[f"df{i}"] = load_dataframe(item["path"], item["ext"])
    if manifest:
        env["df"] = env["df1"]  # 单文件场景下的便捷别名
    # 使用同一个字典作为 globals 和 locals。否则模型代码在函数、lambda 或
    # 推导式中引用 df1/df2 时，只会从 globals 查找，导致 NameError。
    env["__builtins__"] = safe_builtins()
    # 2. 读取并执行模型生成的代码
    with open(code_path, "r", encoding="utf-8") as f:
        code = f.read()
    validate_analysis_code(code)

    buf = LimitedStringIO()
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
                exec(compile(code, "<analysis>", "exec"), env, env)
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
    combined = buf.getvalue() + fd_output[:MAX_CAPTURE_CHARS]
    if buf.truncated or len(fd_output) > MAX_CAPTURE_CHARS:
        combined += "\n[输出过长，已截断]"

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
