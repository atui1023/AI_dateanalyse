import json
import os
import re
import subprocess
import sys
import uuid
from typing import Dict, List, Optional

import pandas as pd
from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

import kb
import llm

app = FastAPI(title="AI 数据分析")

BASE_DIR = os.path.dirname(__file__)
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
KB_DIR = os.path.join(UPLOAD_DIR, "kb")
os.makedirs(KB_DIR, exist_ok=True)

# 内存中保存已上传数据集的信息：dataset_id -> {path, filename, summary}
datasets: Dict[str, dict] = {}

ALLOWED_EXT = (".csv", ".xlsx", ".xls")
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB
MAX_DATASETS = 10  # 单次会话最多挂载的文件数


class ChatRequest(BaseModel):
    # 前端把完整的对话历史传上来：[{"role": "user", "content": "..."}, ...]
    messages: List[Dict[str, str]]
    # 当前挂载的数据集 id 列表（按顺序对应代码环境中的 df1、df2……），为空则普通聊天
    dataset_ids: List[str] = []
    # 对话模式：analysis=数据分析（默认）/ rag=知识库问答
    mode: Optional[str] = None


def load_dataframe(path: str, ext: str) -> pd.DataFrame:
    """读取 CSV / Excel 为 DataFrame，CSV 自动尝试 gbk 编码"""
    if ext == ".csv":
        try:
            return pd.read_csv(path)
        except UnicodeDecodeError:
            return pd.read_csv(path, encoding="gbk")
    return pd.read_excel(path)


def build_summary(df: pd.DataFrame) -> dict:
    """生成给前端展示和给模型看的数据摘要"""
    columns = []
    for col in df.columns:
        series = df[col].dropna()
        samples = [str(v) for v in series.head(3).tolist()]
        columns.append({
            "name": str(col),
            "dtype": str(df[col].dtype),
            "samples": samples,
        })
    preview = df.head(5).fillna("").astype(str).values.tolist()
    return {
        "rows": int(df.shape[0]),
        "cols": int(df.shape[1]),
        "columns": columns,
        "preview_columns": [str(c) for c in df.columns],
        "preview_rows": preview,
    }


def summary_to_text(summary: dict) -> str:
    lines = [f"数据维度：{summary['rows']} 行 × {summary['cols']} 列", "字段信息："]
    for c in summary["columns"]:
        samples = "、".join(c["samples"]) if c["samples"] else "无"
        lines.append(f"- {c['name']}（类型 {c['dtype']}），示例值：{samples}")
    return "\n".join(lines)


def dataset_summary_text(index: int, ds: dict) -> str:
    """单个数据集的摘要，标注它在代码环境中的变量名（df1、df2……）"""
    return f"【变量 df{index}，文件：{ds['filename']}】\n{summary_to_text(ds['summary'])}"


@app.post("/upload")
async def upload(file: UploadFile = File(...)):
    """上传数据文件，解析后返回数据集 id 和摘要"""
    filename = file.filename or "未命名文件"
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(status_code=400, detail="仅支持 CSV / Excel（.csv / .xlsx / .xls）文件")

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="文件大小不能超过 10MB")

    dataset_id = uuid.uuid4().hex
    save_path = os.path.join(UPLOAD_DIR, dataset_id + ext)
    with open(save_path, "wb") as f:
        f.write(content)

    try:
        df = load_dataframe(save_path, ext)
    except Exception as e:
        os.remove(save_path)
        raise HTTPException(status_code=400, detail=f"文件解析失败：{e}")

    summary = build_summary(df)
    datasets[dataset_id] = {
        "path": save_path,
        "ext": ext,
        "filename": filename,
        "summary": summary,
    }
    return {"dataset_id": dataset_id, "filename": filename, "summary": summary}


@app.delete("/datasets/{dataset_id}")
def remove_dataset(dataset_id: str):
    """移除数据集：删除内存记录和磁盘文件"""
    ds = datasets.pop(dataset_id, None)
    if ds and os.path.exists(ds["path"]):
        os.remove(ds["path"])
    return {"ok": True}


# ———————— 知识库（RAG）接口 ————————


@app.post("/kb/upload")
async def kb_upload(file: UploadFile = File(...)):
    """上传文档（TXT/MD/PDF）入库：解析 → 切分 → 向量化 → 存入 Chroma"""
    filename = os.path.basename(file.filename or "未命名文档")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in kb.KB_EXTENSIONS:
        raise HTTPException(status_code=400, detail="知识库仅支持 TXT / MD / PDF / CSV 文档")

    content = await file.read()
    if len(content) > kb.MAX_KB_FILE_SIZE:
        raise HTTPException(status_code=400, detail="文档大小不能超过 20MB")
    if not content.strip():
        raise HTTPException(status_code=400, detail="文件内容为空，请先在文档中写入文字再上传")

    save_path = os.path.join(KB_DIR, f"{uuid.uuid4().hex}{ext}")
    with open(save_path, "wb") as f:
        f.write(content)

    try:
        result = kb.ingest(save_path, ext, filename)
    except Exception as e:
        os.remove(save_path)
        raise HTTPException(status_code=400, detail=f"文档入库失败：{e}")
    return result


@app.get("/kb/documents")
def kb_documents():
    """列出知识库中的全部文档"""
    return kb.list_documents()


@app.delete("/kb/documents/{doc_id}")
def kb_delete(doc_id: str):
    """删除知识库文档：删向量块 + 删磁盘文件"""
    target = next((d for d in kb.list_documents() if d["doc_id"] == doc_id), None)
    kb.delete_document(doc_id)
    if target and target.get("path") and os.path.exists(target["path"]):
        os.remove(target["path"])
    return {"ok": True}


def extract_code(reply: str) -> Optional[str]:
    """从模型回复中提取 ```python 代码块"""
    m = re.search(r"```(?:python)?\s*\n?(.*?)```", reply, re.S)
    return m.group(1).strip() if m else None


def run_analysis(active_datasets: List[dict], code: str) -> dict:
    """在独立子进程中执行模型生成的 pandas 代码，30 秒超时。

    active_datasets 按顺序对应代码环境中的 df1、df2……
    """
    code_path = os.path.join(UPLOAD_DIR, f"code_{uuid.uuid4().hex}.py")
    with open(code_path, "w", encoding="utf-8") as f:
        f.write(code)

    manifest = json.dumps(
        [{"path": d["path"], "ext": d["ext"]} for d in active_datasets],
        ensure_ascii=False,
    )

    try:
        proc = subprocess.run(
            [sys.executable, os.path.join(BASE_DIR, "runner.py"), code_path, manifest],
            capture_output=True,
            text=True,
            encoding="utf-8",
            timeout=30,
        )
    except subprocess.TimeoutExpired:
        return {"error": "代码执行超时（超过 30 秒），请简化分析逻辑后重试"}
    finally:
        if os.path.exists(code_path):
            os.remove(code_path)

    output = proc.stdout or ""
    marker_ok = "===RUNNER_OK==="
    marker_err = "===RUNNER_ERR==="
    if marker_ok in output:
        payload = output.split(marker_ok, 1)[1].strip()
        try:
            return json.loads(payload)
        except json.JSONDecodeError:
            return {"error": "结果解析失败"}
    if marker_err in output:
        payload = output.split(marker_err, 1)[1].strip()
        try:
            data = json.loads(payload)
            return {"error": f"代码执行出错：{data.get('error', '未知错误')}", "stdout": data.get("stdout", "")}
        except json.JSONDecodeError:
            pass
    return {"error": f"代码执行失败：{(proc.stderr or '未知错误')[:300]}"}


def sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/chat")
def chat(req: ChatRequest):
    """流式对话接口：SSE 逐字返回模型回复。

    三种场景：数据分析（挂载数据集）/ RAG 知识库问答（mode=rag）/ 普通聊天
    """
    # 按前端传入顺序收集当前挂载的数据集（对应 df1、df2……）
    active = [datasets[i] for i in req.dataset_ids if i in datasets]

    sources = []  # 引用出处（RAG 模式：知识库资料；数据分析模式：参考的业务知识）
    if req.mode == "rag":
        question = req.messages[-1]["content"] if req.messages else ""
        # 多轮追问先改写成独立问题再检索（"那华南呢"→"华南地区的销售额是多少"），失败自动回退原问题
        search_query = llm.rewrite_question(req.messages[:-1], question)
        try:
            docs = kb.retrieve(search_query, k=kb.TOP_K)
            context_text, sources = kb.build_context(docs)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"知识库检索失败：{e}")
        system_content = llm.build_rag_prompt(context_text)
    elif active:
        summary_text = "\n\n".join(dataset_summary_text(i + 1, ds) for i, ds in enumerate(active))
        # 数据分析时自动参考永久知识库中的业务规则（Top-3）；库为空或检索失败都不影响分析
        question = req.messages[-1]["content"] if req.messages else ""
        kb_context = ""
        try:
            if question and kb.doc_count() > 0:
                docs = kb.retrieve(question, k=3)
                if docs:
                    kb_context, sources = kb.build_context(docs)
        except Exception:
            kb_context, sources = "", []
        system_content = llm.build_system_prompt(summary_text, kb_context)
    else:
        system_content = llm.build_system_prompt(None)

    messages = [{"role": "system", "content": system_content}] + req.messages

    def generate():
        try:
            full_reply = ""
            for delta in llm.stream_completion(messages):
                full_reply += delta
                yield sse({"type": "content", "content": delta})

            if sources:
                # 回传引用出处：RAG 模式是知识库资料，数据分析模式是参考的业务知识
                yield sse({"type": "sources", "sources": sources})
            if req.mode != "rag" and active:
                # 数据分析场景：提取代码并执行，把结果回传前端
                code = extract_code(full_reply)
                if code:
                    result = run_analysis(active, code)
                    yield sse({"type": "result", **result})
                else:
                    yield sse({"type": "result", "error": "模型没有生成可执行的分析代码，请换个问法试试"})

            yield "data: [DONE]\n\n"
        except llm.ModelConnectionError as e:
            # 多次重试后仍连不上模型服务，消息本身已是友好中文提示
            print("chat connection error:", e, file=sys.stderr)
            yield sse({"type": "error", "error": str(e)})
        except Exception as e:
            print("chat error:", type(e).__name__, e, file=sys.stderr)
            yield sse({"type": "error", "error": f"模型调用出错（{type(e).__name__}），请重试；若反复出现请检查 .env 中的 API_KEY / BASE_URL"})

    return StreamingResponse(generate(), media_type="text/event-stream")


@app.get("/")
def index():
    return FileResponse(os.path.join(BASE_DIR, "static", "index.html"))
