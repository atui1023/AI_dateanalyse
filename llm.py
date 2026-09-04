"""模型调用层：集中管理 OpenAI 兼容客户端、模型配置与 prompt 模板。

将来若要引入 LangChain / LlamaIndex 等框架，只需改造本文件，
路由（main.py）、沙箱执行（runner.py）和前端都无需改动。
"""
import os
import time
from typing import Dict, Iterator, List, Optional

from dotenv import load_dotenv
from openai import APIConnectionError, APITimeoutError, OpenAI

# 从 .env 文件读取配置
load_dotenv()

API_KEY = os.getenv("API_KEY")
BASE_URL = os.getenv("BASE_URL")
MODEL = os.getenv("MODEL", "qwen-plus")

if not API_KEY:
    raise RuntimeError("未读取到 API_KEY，请检查项目目录下的 .env 文件")

client = OpenAI(api_key=API_KEY, base_url=BASE_URL)

# 普通聊天的系统提示词
SYSTEM_PROMPT = "You are a helpful assistant. 请使用中文回答用户的问题。"

# 数据分析场景的系统提示词（{summary} 处填充一个或多个数据集的摘要）
ANALYSIS_PROMPT_TEMPLATE = """你是一名数据分析助手。用户上传了一个或多个数据集，在代码环境中已按顺序加载为 pandas DataFrame 变量 `df1`、`df2`、……（`pd`、`np` 也可直接使用，`df` 等价于 `df1`）。

数据集信息：
{summary}

请根据用户的问题编写 pandas 分析代码，要求：
1. 只做只读分析，不要读写文件、不要联网。环境中只有 pandas、numpy，禁止 import matplotlib、seaborn、plotly 等任何绘图库（没有安装，会报错），图表只能通过下面的 chart 变量交给前端渲染
2. 引用数据时必须使用对应的变量名（df1、df2……），不要混淆不同文件的数据；需要跨文件关联分析时，用 pd.merge / pd.concat / join 等，先在代码中完成合并再统计
3. 将最终的表格结果赋值给变量 `result`（pandas DataFrame 类型）；若问题不需要表格结果，可不赋值
4. 用 print() 输出你的分析结论和关键数字（中文）
5. 用户要求图表、或结果适合可视化时（排名对比用 bar、趋势用 line、占比用 pie），必须赋值变量 `chart`，值为 ECharts 的 option 字典（Python dict，不是字符串），数据必须来自上面真实计算出的结果，不要编造：
   - 柱状/折线图：{{"title": {{"text": "标题"}}, "xAxis": {{"type": "category", "data": 类别列表}}, "yAxis": {{"type": "value"}}, "series": [{{"type": "bar", "data": 数值列表}}]}}
   - 饼图：{{"title": {{"text": "标题"}}, "series": [{{"type": "pie", "data": [{{"name": 类别, "value": 数值}}, ...]}}]}}
   - 多系列对比时 series 中放多个字典，每个带 "name"；不需要图表时不要赋值 chart
6. 只输出一个 ```python 代码块，代码块之外可以有简短的中文说明
"""


def build_system_prompt(summary_text: Optional[str] = None) -> str:
    """根据是否挂载数据集，返回对应的系统提示词"""
    if summary_text:
        return ANALYSIS_PROMPT_TEMPLATE.format(summary=summary_text)
    return SYSTEM_PROMPT


class ModelConnectionError(Exception):
    """模型服务多次连接失败（网络波动等），用于给前端友好提示"""


def stream_completion(messages: List[Dict[str, str]], max_retries: int = 3) -> Iterator[str]:
    """流式调用模型，逐段产出回复文本内容。

    :param messages: 含 system 消息的完整对话历史
    :param max_retries: 连接失败/超时时的最大尝试次数（指数退避）

    只在「还没吐出任何内容」的建连阶段重试，避免已输出内容后重复；
    连接成功后中断的错误直接向上抛出。
    """
    # —— 建连阶段：创建流并拿到第一个 chunk，失败可安全重试（尚未输出任何内容）——
    def _connect():
        stream = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            stream=True,
            temperature=0.2,
        )
        iterator = iter(stream)
        try:
            first = next(iterator)
        except StopIteration:
            return None, None
        return iterator, first

    last_error: Optional[Exception] = None
    for attempt in range(max_retries):
        try:
            iterator, first = _connect()
            break
        except (APIConnectionError, APITimeoutError) as e:
            # 网络波动/超时：退避后重试（1.5s、3s、4.5s……）
            last_error = e
            if attempt < max_retries - 1:
                time.sleep(1.5 * (attempt + 1))
    else:
        raise ModelConnectionError(
            f"模型服务连续 {max_retries} 次连接失败，可能是网络波动，请稍后重发消息"
        ) from last_error

    if iterator is None:
        return

    # —— 连接已建立：正常产出内容，此阶段不再重试（避免重复输出）——
    if first.choices and first.choices[0].delta.content:
        yield first.choices[0].delta.content
    for chunk in iterator:
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta.content
        if delta:
            yield delta
