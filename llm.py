"""模型调用层（LangChain Chain 版）：用 LCEL 管道组织模型调用。

组件结构：
    组件①chat_model      ChatOpenAI 模型实例（内置连接重试）
    组件②提示模板        SYSTEM / ANALYSIS / RAG / REWRITE 四套
    组件③chat_chain      chat_model | StrOutputParser()
    组件④rewrite_chain   改写模板 | 改写模型 | 解析器 | 清洗函数

对外接口与旧版完全一致（main.py 无需改动）：
    build_system_prompt / build_rag_prompt / rewrite_question / stream_completion / ModelConnectionError
将来升级流程（多路召回、自动纠错、路由分发）时只改本文件的链定义。
"""
import os
from typing import Dict, Iterator, List, Optional

from dotenv import load_dotenv
from langchain_core.output_parsers import StrOutputParser
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableLambda
from langchain_openai import ChatOpenAI
from openai import APIConnectionError, APITimeoutError

# 从 .env 文件读取配置
load_dotenv()

API_KEY = os.getenv("API_KEY")
BASE_URL = os.getenv("BASE_URL")
MODEL = os.getenv("MODEL", "qwen-plus")

if not API_KEY:
    raise RuntimeError("未读取到 API_KEY，请检查项目目录下的 .env 文件")

# ============ 组件①：模型实例 ============
# 主对话模型：max_retries=3 让底层 SDK 在「建连阶段」自动重试（此时尚无内容输出，不会重复），
# 流式开始后中断则原样抛错，由上层处理
chat_model = ChatOpenAI(
    model=MODEL,
    api_key=API_KEY,
    base_url=BASE_URL,
    temperature=0.2,   # 低温度：分析类任务要稳定、少发挥
    timeout=120,
    max_retries=3,
)

# 问题改写模型：要求绝对确定，temperature=0；失败可接受（回退原问题），max_retries=1 即可
rewrite_model = ChatOpenAI(
    model=MODEL,
    api_key=API_KEY,
    base_url=BASE_URL,
    temperature=0.0,
    timeout=15,
    max_retries=1,
)

# ============ 组件②：提示模板 ============
# 普通聊天
SYSTEM_PROMPT = "You are a helpful assistant. 请使用中文回答用户的问题。"

# 数据分析场景的系统提示词（{summary} 处填充一个或多个数据集的摘要）
# 注：模板含大量 JSON 花括号示例，故用 str.format 填充，{{}} 转义保持原样
ANALYSIS_PROMPT_TEMPLATE = """你是一名严谨的数据分析师。用户上传了一个或多个数据集，在代码环境中已按顺序加载为 pandas DataFrame 变量 `df1`、`df2`、……（`pd`、`np` 也可直接使用，`df` 等价于 `df1`）。

数据集信息：
{summary}

{knowledge}

请根据用户的问题编写 pandas 分析代码，要求：

【代码规范】
1. 只做只读分析：不读写文件、不联网；可用库：pandas（pd）、numpy（np）、scipy、scikit-learn（sklearn）、statsmodels（均已预装，可直接 import）；禁止 import matplotlib、seaborn、plotly 等绘图库（未安装），图表只能通过下面的 chart 变量交给前端渲染
2. 引用列名必须与摘要中原样一致（注意空格和大小写）；不确定列是否存在时，先 print(df.columns) 确认再取列
3. 需要跨文件关联分析时，用 pd.merge / pd.concat / join 等，先在代码中确认关联键存在并完成合并，再统计
4. 代码要稳健：过滤、聚合前先处理缺失值（dropna 或 fillna，并在结论中说明口径）；分组结果按业务含义排序（如排名类默认降序）
5. 若上方【业务知识库参考】定义了指标口径、计算公式、过滤条件或单位换算，必须按其执行，不得使用默认算法；业务知识与数据实际列对不上时，以数据为准并在结论中说明差异

【结果输出】（务必严格遵守）
6. 【最重要】必须把最终表格赋值给变量 `result`（如 result = df1.groupby(...)....reset_index()），这是把结果传给用户的唯一途径；裸写一个表达式（如只写 region_sales）不会输出任何东西。若问题不需要表格结果，可不赋值
7. 用 print() 输出中文分析结论：先直接回答问题（关键数字用千分位/百分比格式），再用一句话给出业务解读或建议；不要罗列计算过程
8. 用户要求图表、或结果适合可视化时（排名对比用 bar、趋势用 line、占比用 pie），必须赋值变量 `chart`，值为 ECharts 的 option 字典（Python dict，不是字符串），数据必须来自上面真实计算出的结果，不要编造：
   - 柱状/折线图：{{"title": {{"text": "标题"}}, "xAxis": {{"type": "category", "data": 类别列表}}, "yAxis": {{"type": "value"}}, "series": [{{"type": "bar", "data": 数值列表}}]}}
   - 饼图：{{"title": {{"text": "标题"}}, "series": [{{"type": "pie", "data": [{{"name": 类别, "value": 数值}}, ...]}}]}}
   - 多系列对比时 series 中放多个字典，每个带 "name"；不需要图表时不要赋值 chart
9. 只输出一个 ```python 代码块，代码块之外可以有简短的中文说明
10. 若用户的问题在数据中无法回答（缺少列、口径不明），不要硬算：在说明中解释缺少什么，并给出最接近的可行分析
"""

# RAG 知识库问答的系统提示词（{context} 处填充检索到的参考资料）
RAG_PROMPT_TEMPLATE = """你是一个知识库问答助手。下面是从用户文档库中检索到的参考资料，请**仅根据这些资料**回答用户问题。

参考资料：
{context}

要求：
1. 答案必须基于参考资料，禁止编造；关键结论后用 [资料1]、[资料2] 标注出处
2. 涉及多份资料时综合回答并分别标注；资料之间有冲突时，明确指出冲突并列出各方说法，不要擅自取舍
3. 资料不足以回答时，明确回答"文档库中没有找到相关内容"；可以补充通用知识，但必须注明"（以下为通用知识，非文档内容）"
4. 资料中的数字、金额、日期、条款编号必须原样引用，不要换算或概括出错
5. 用中文回答，条理清晰；回答末尾单独一行注明来源，格式：来源：《文件名》
"""

# 检索前的问题改写提示词：把多轮对话中的追问补全为独立问题，提升检索命中率
# 用 ChatPromptTemplate 表达（system+user 双消息），变量值由代码安全注入
REWRITE_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", "只输出改写后的问题本身，不要任何解释。"),
        (
            "user",
            "你的任务是把用户的追问改写成一个独立、完整的问题，用于知识库检索。\n\n"
            "对话历史：\n{history}\n\n"
            "用户当前输入：{question}\n\n"
            "规则：\n"
            "1. 结合对话历史补全指代和省略（如\"那华南呢\"→\"华南地区的销售额是多少\"），不要改变用户的问题意图，不要自行扩展新问题\n"
            "2. 只输出改写后的问题本身，不要任何解释\n"
            "3. 若当前输入已经是完整独立的问题，原样输出",
        ),
    ]
)


def build_system_prompt(summary_text: Optional[str] = None, knowledge_text: Optional[str] = None) -> str:
    """根据是否挂载数据集/是否有业务知识，返回对应的系统提示词"""
    if summary_text:
        knowledge_block = (
            f"【业务知识库参考】（来自用户永久知识库，分析时必须遵守其中的口径与规则）\n{knowledge_text}"
            if knowledge_text
            else "【业务知识库参考】（本次无参考资料，按通用口径分析）"
        )
        return ANALYSIS_PROMPT_TEMPLATE.format(summary=summary_text, knowledge=knowledge_block)
    return SYSTEM_PROMPT


def build_rag_prompt(context: str) -> str:
    """RAG 知识库问答的系统提示词"""
    return RAG_PROMPT_TEMPLATE.format(context=context)


# ============ 组件③：主对话链 ============
# chat_model | 解析器：输入 messages（dict 列表），输出纯文本流
chat_chain = chat_model | StrOutputParser()


# ============ 组件④：问题改写链 ============
def _clean_rewrite(text: str) -> str:
    """清洗改写输出：去引号、取第一行、限长，防御模型偶尔的多余输出"""
    text = (text or "").strip()
    if not text:
        return ""
    return text.strip('"「」『』').splitlines()[0][:200]


rewrite_chain = REWRITE_PROMPT | rewrite_model | StrOutputParser() | RunnableLambda(_clean_rewrite)


def rewrite_question(history: List[Dict[str, str]], question: str) -> str:
    """把多轮对话中的追问改写成独立完整的问题（用于 RAG 检索）。

    改写失败时静默回退为原问题，不影响主流程。
    """
    history_text = "\n".join(
        f"{'用户' if m['role'] == 'user' else '助手'}：{m.get('content', '')[:300]}"
        for m in history[-6:]
        if m.get("content")
    )
    try:
        rewritten = rewrite_chain.invoke({"history": history_text or "（无）", "question": question})
        return rewritten or question
    except Exception:
        return question


class ModelConnectionError(Exception):
    """模型服务连接失败（网络波动等），用于给前端友好提示"""


def stream_completion(messages: List[Dict[str, str]], max_retries: int = 3) -> Iterator[str]:
    """流式调用主对话链，逐段产出回复文本。

    :param messages: 含 system 消息的完整对话历史（dict 列表）
    :param max_retries: 保留参数（重试由 chat_model 的底层 SDK 承担）

    建连阶段（第一个 chunk 之前）的连接错误转成 ModelConnectionError（SDK 已自动重试 3 次）；
    流式输出开始后的中断原样向上抛出，避免上层重复输出。
    """
    iterator = iter(chat_chain.stream(messages))
    try:
        first = next(iterator)
    except StopIteration:
        return
    except (APIConnectionError, APITimeoutError) as e:
        raise ModelConnectionError(
            f"模型服务连续 {max_retries} 次连接失败，可能是网络波动，请稍后重发消息"
        ) from e
    yield first
    for chunk in iterator:
        if chunk:
            yield chunk
