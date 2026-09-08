import json
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

import auth
import kb
import llm
from auth import get_current_user
# 审计日志通过 auth.log_action 调用（用模块前缀更清晰，避免和本地变量混淆）
from db import (
    AnalysisResult, ChatMessage, Session as DbSession, User, ensure_default_user, get_session,
)

app = FastAPI(title="AI 数据分析")

# SessionMiddleware：cookie 签名存储 user_id，30 天有效期
# secret_key 从 .env 读取，缺失时用固定串（开发环境兜底，生产必须配置）
_SESSION_SECRET = os.getenv("SESSION_SECRET", "dev-only-insecure-secret-please-change")
app.add_middleware(
    SessionMiddleware,
    secret_key=_SESSION_SECRET,
    session_cookie="session",
    max_age=30 * 24 * 3600,  # 30 天
    same_site="lax",
    https_only=False,
)

# 注册鉴权路由（/auth/login / /auth/register / /auth/logout / /auth/me）
app.include_router(auth.router)

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
    # 会话 id（首次为空时后端自动新建并返回，前端需保存以继续追问）
    session_id: Optional[str] = None


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
async def upload(file: UploadFile = File(...), user: User = Depends(get_current_user)):
    """上传表格数据文件（CSV/Excel）：同步解析 pandas 摘要用于数据分析，
    同时登记进知识库“临时文件”区并后台异步向量化（上传后即可被 RAG 检索）。"""
    filename = os.path.basename(file.filename or "未命名文件")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in ALLOWED_EXT:
        raise HTTPException(status_code=400, detail="仅支持 CSV / Excel（.csv / .xlsx / .xls）文件")

    content = await file.read()
    if len(content) > MAX_FILE_SIZE:
        raise HTTPException(status_code=400, detail="文件大小不能超过 10MB")

    # 统一存入知识库目录，由 kb 模块登记并触发后台向量化
    save_path = os.path.join(KB_DIR, f"{uuid.uuid4().hex}{ext}")
    with open(save_path, "wb") as f:
        f.write(content)

    try:
        df = load_dataframe(save_path, ext)
    except Exception as e:
        if os.path.exists(save_path):
            os.remove(save_path)
        raise HTTPException(status_code=400, detail=f"文件解析失败：{e}")

    doc = kb.register_document(save_path, ext, filename, user_id=user.id)  # 状态=解析中，后台向量化
    summary = build_summary(df)
    datasets[doc["doc_id"]] = {
        "path": save_path,
        "ext": ext,
        "filename": filename,
        "summary": summary,
        "doc_id": doc["doc_id"],
    }
    return {
        "dataset_id": doc["doc_id"],
        "doc_id": doc["doc_id"],
        "filename": filename,
        "summary": summary,
        "status": doc["status"],
    }


@app.delete("/datasets/{dataset_id}")
def remove_dataset(dataset_id: str, user: User = Depends(get_current_user)):
    """取消挂载数据集：仅移除内存中的分析挂载（df1、df2……），
    文件本身与向量数据保留在知识库“临时文件”区，如需彻底删除请用知识库删除接口。"""
    datasets.pop(dataset_id, None)
    return {"ok": True}


@app.post("/kb/documents/{doc_id}/mount")
def kb_mount(doc_id: str, user: User = Depends(get_current_user)):
    """把知识库中的表格文件挂载为分析数据集（df1、df2……），返回数据摘要"""
    try:
        rec = kb.get_document(doc_id, user_id=user.id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    if rec["ext"] not in ALLOWED_EXT:
        raise HTTPException(status_code=400, detail="仅 CSV / Excel 表格文件可用于数据分析")
    if not os.path.exists(rec["path"]):
        raise HTTPException(status_code=400, detail="原始文件已丢失，请重新上传")
    try:
        df = load_dataframe(rec["path"], rec["ext"])
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"文件解析失败：{e}")
    summary = build_summary(df)
    datasets[doc_id] = {
        "path": rec["path"],
        "ext": rec["ext"],
        "filename": rec["filename"],
        "summary": summary,
        "doc_id": doc_id,
    }
    return {"dataset_id": doc_id, "filename": rec["filename"], "summary": summary}


# ———————— 知识库（RAG）接口 ————————


@app.post("/kb/upload")
async def kb_upload(request: Request, file: UploadFile = File(...), folder_id: str = Form(None),
                    user: User = Depends(get_current_user)):
    """上传文档（TXT/MD/PDF/CSV/Excel）：立即登记进知识库并返回，
    后台异步完成 解析 → 切分 → 向量化（状态可通过 /kb/documents 轮询）。
    folder_id 可选；不传或无效时自动归入系统“临时文件”知识库（兜底机制）。"""
    filename = os.path.basename(file.filename or "未命名文档")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in kb.KB_EXTENSIONS:
        raise HTTPException(status_code=400, detail="仅支持 TXT / MD / PDF / CSV / Excel 文档")

    content = await file.read()
    if len(content) > kb.MAX_KB_FILE_SIZE:
        raise HTTPException(status_code=400, detail="文档大小不能超过 20MB")
    if ext in (".txt", ".md", ".csv") and not content.strip():
        raise HTTPException(status_code=400, detail="文件内容为空，请先在文档中写入文字再上传")

    save_path = os.path.join(KB_DIR, f"{uuid.uuid4().hex}{ext}")
    with open(save_path, "wb") as f:
        f.write(content)

    doc = kb.register_document(save_path, ext, filename, folder_id=folder_id, user_id=user.id)
    auth.log_action(user.id, "upload_doc", "document", doc["doc_id"],
                    detail=f"upload {filename} ({ext})", ip=auth._client_ip(request))
    return {"doc_id": doc["doc_id"], "filename": doc["filename"], "status": doc["status"]}


@app.get("/kb/documents")
def kb_documents(user: User = Depends(get_current_user)):
    """列出知识库中的全部文档（含解析状态：parsing/ready/failed）"""
    return kb.list_documents(user_id=user.id)


@app.delete("/kb/documents/{doc_id}")
def kb_delete(doc_id: str, request: Request, user: User = Depends(get_current_user)):
    """删除知识库文档：同步清理向量块/向量（防幽灵数据）+ 注册表记录 + 磁盘文件 + 分析挂载"""
    rec = kb.delete_document(doc_id, user_id=user.id)
    datasets.pop(doc_id, None)  # 若正挂载为 df，一并取消挂载
    if rec and rec.get("path") and os.path.exists(rec["path"]):
        try:
            os.remove(rec["path"])
        except OSError:
            pass
    auth.log_action(user.id, "delete_doc", "document", doc_id,
                    detail=f"delete {rec.get('filename', '') if rec else ''}",
                    ip=auth._client_ip(request))
    return {"ok": True}


@app.post("/kb/documents/{doc_id}/retry")
def kb_retry(doc_id: str, user: User = Depends(get_current_user)):
    """重试解析失败的文档（重新触发后台向量化）"""
    try:
        doc = kb.retry_document(doc_id, user_id=user.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"doc_id": doc["doc_id"], "status": doc["status"]}


# ———————— 知识库（文件夹：分类 + 按需激活）接口 ————————


@app.get("/kb/folders")
def kb_folders_list(user: User = Depends(get_current_user)):
    """知识库列表（含激活状态、文件数与向量段数）"""
    return kb.list_folders(user_id=user.id)


@app.post("/kb/folders")
def kb_folder_create(payload: dict, user: User = Depends(get_current_user)):
    """新建知识库：{"name": "..."}"""
    try:
        return kb.create_folder(payload.get("name", ""), user_id=user.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.patch("/kb/folders/{folder_id}")
def kb_folder_update(folder_id: str, payload: dict, user: User = Depends(get_current_user)):
    """更新知识库：{"name": "..."} 重命名 / {"active": true|false} 切换激活"""
    try:
        if "name" in payload:
            return kb.rename_folder(folder_id, payload["name"], user_id=user.id)
        if "active" in payload:
            return kb.set_folder_active(folder_id, payload["active"], user_id=user.id)
        raise HTTPException(status_code=400, detail="无有效字段（支持 name / active）")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.delete("/kb/folders/{folder_id}")
def kb_folder_delete(folder_id: str, request: Request, user: User = Depends(get_current_user)):
    """删除知识库：其下文件自动退回系统“临时文件”库（仅改外键，向量数据不丢失）"""
    try:
        moved = kb.delete_folder(folder_id, user_id=user.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    auth.log_action(user.id, "delete_folder", "folder", folder_id,
                    detail=f"moved {moved} docs to temp", ip=auth._client_ip(request))
    return {"ok": True, "moved_docs": moved}


@app.patch("/kb/documents/{doc_id}/folder")
def kb_doc_move(doc_id: str, payload: dict, user: User = Depends(get_current_user)):
    """移动文档到指定知识库：{"folder_id": "..."}（仅更新外键，不重新向量化）"""
    try:
        kb.move_document(doc_id, payload.get("folder_id", ""), user_id=user.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"ok": True}


@app.patch("/kb/documents/{doc_id}/active")
def kb_doc_active(doc_id: str, payload: dict, user: User = Depends(get_current_user)):
    """切换单个文档是否参与检索：{"active": true|false}"""
    try:
        return kb.set_doc_active(doc_id, bool(payload.get("active", True)), user_id=user.id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


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

    def _parse_after(marker: str) -> Optional[dict]:
        """取最后一个标记后的内容，用 raw_decode 容错解析 JSON（忽略尾部垃圾）"""
        idx = output.rfind(marker)
        if idx < 0:
            return None
        rest = output[idx + len(marker):]
        brace = rest.find("{")
        if brace < 0:
            return None
        try:
            data, _ = json.JSONDecoder().raw_decode(rest[brace:])
            return data
        except json.JSONDecodeError:
            return None

    ok_data = _parse_after(marker_ok)
    if ok_data is not None:
        return ok_data
    err_data = _parse_after(marker_err)
    if err_data is not None:
        return {"error": f"代码执行出错：{err_data.get('error', '未知错误')}", "stdout": err_data.get("stdout", "")}
    # 兜底：runner 未输出任何标记（进程异常退出），把返回码和输出片段一并返回便于定位
    err_tail = (proc.stderr or "").strip()
    out_tail = output.strip()[-200:]
    detail = err_tail or out_tail or "进程无任何输出"
    return {"error": f"代码执行失败（退出码 {proc.returncode}）：{detail[:300]}"}


def sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/chat")
def chat(req: ChatRequest, user: User = Depends(get_current_user)):
    """流式对话接口：SSE 逐字返回模型回复。

    三种场景：数据分析（挂载数据集）/ RAG 知识库问答（mode=rag）/ 普通聊天
    持久化：用户消息先落库，流式结束追加 assistant 消息 + 分析结果
    """
    # 按前端传入顺序收集当前挂载的数据集（对应 df1、df2……）
    active = [datasets[i] for i in req.dataset_ids if i in datasets]

    # —— 会话归属校验 / 新建会话 ——
    session_id = req.session_id
    with get_session() as db:
        if session_id:
            sess = db.get(DbSession, session_id)
            if sess is None or sess.user_id != user.id:
                raise HTTPException(status_code=404, detail="会话不存在或无权访问")
        else:
            session_id = uuid.uuid4().hex
            sess = DbSession(session_id=session_id, user_id=user.id,
                             mode=req.mode or "chat", title=None)
            db.add(sess)
            db.commit()

    question = req.messages[-1]["content"] if req.messages else ""
    # —— 落库用户消息（在流式开始前，保证问/答顺序） ——
    with get_session() as db:
        db.add(ChatMessage(session_id=session_id, user_id=user.id,
                           role="user", content=question))
        # 首条消息自动生成会话标题（取前 24 字符）
        sess = db.get(DbSession, session_id)
        if sess and sess.title is None:
            sess.title = (question.strip() or "新会话")[:24]
            sess.updated_at = datetime.now()
        db.commit()

    sources = []  # 引用出处（RAG 模式：知识库资料；数据分析模式：参考的业务知识）
    if req.mode == "rag":
        # 多轮追问先改写成独立问题再检索（"那华南呢"→"华南地区的销售额是多少"），失败自动回退原问题
        search_query = llm.rewrite_question(req.messages[:-1], question)
        try:
            docs = kb.retrieve(search_query, k=kb.TOP_K,
                               folder_ids=kb.active_folder_ids(user_id=user.id),
                               doc_ids=kb.active_doc_ids(user_id=user.id),
                               user_id=user.id)
            context_text, sources = kb.build_context(docs)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"知识库检索失败：{e}")
        system_content = llm.build_rag_prompt(context_text)
    elif active:
        summary_text = "\n\n".join(dataset_summary_text(i + 1, ds) for i, ds in enumerate(active))
        # 数据分析时自动参考永久知识库中的业务规则（Top-3）；库为空或检索失败都不影响分析
        kb_context = ""
        try:
            if question and kb.doc_count(user_id=user.id) > 0:
                # 分析时也只参考激活知识库和激活文件内的业务规则
                docs = kb.retrieve(question, k=3,
                                   folder_ids=kb.active_folder_ids(user_id=user.id),
                                   doc_ids=kb.active_doc_ids(user_id=user.id),
                                   user_id=user.id)
                if docs:
                    kb_context, sources = kb.build_context(docs)
        except Exception:
            kb_context, sources = "", []
        system_content = llm.build_system_prompt(summary_text, kb_context)
    else:
        system_content = llm.build_system_prompt(None)

    messages = [{"role": "system", "content": system_content}] + req.messages

    # 闭包用：把 session_id 和 user.id 带进 generate
    _sid = session_id
    _uid = user.id
    _mode = req.mode

    def generate():
        full_reply = ""
        analysis_payload = None  # 数据分析结果（如果有）
        try:
            for delta in llm.stream_completion(messages):
                full_reply += delta
                yield sse({"type": "content", "content": delta})

            if sources:
                # 回传引用出处：RAG 模式是知识库资料，数据分析模式是参考的业务知识
                yield sse({"type": "sources", "sources": sources})
            if _mode != "rag" and active:
                # 数据分析场景：提取代码并执行，把结果回传前端
                code = extract_code(full_reply)
                if code:
                    result = run_analysis(active, code)
                    analysis_payload = {"code": code, **result}
                    yield sse({"type": "result", **result})
                else:
                    yield sse({"type": "result", "error": "模型没有生成可执行的分析代码，请换个问法试试"})

            # 把新建的 session_id 回传给前端（首次对话时前端要保存）
            if not req.session_id:
                yield sse({"type": "session", "session_id": _sid})

            yield "data: [DONE]\n\n"
        except llm.ModelConnectionError as e:
            # 多次重试后仍连不上模型服务，消息本身已是友好中文提示
            print("chat connection error:", e, file=sys.stderr)
            yield sse({"type": "error", "error": str(e)})
        except Exception as e:
            print("chat error:", type(e).__name__, e, file=sys.stderr)
            yield sse({"type": "error", "error": f"模型调用出错（{type(e).__name__}），请重试；若反复出现请检查 .env 中的 API_KEY / BASE_URL"})
        finally:
            # —— 落库 assistant 消息 + 分析结果（无论成功失败都存，便于回看） ——
            if full_reply:
                try:
                    with get_session() as db:
                        db.add(ChatMessage(session_id=_sid, user_id=_uid,
                                           role="assistant", content=full_reply))
                        # 更新会话时间戳
                        sess = db.get(DbSession, _sid)
                        if sess:
                            sess.updated_at = datetime.now()
                        # 分析结果单独存表
                        if analysis_payload and _mode != "rag" and active:
                            db.add(AnalysisResult(
                                user_id=_uid, session_id=_sid,
                                question=question,
                                code=analysis_payload.get("code"),
                                stdout=analysis_payload.get("stdout"),
                                table_json=(json.dumps(analysis_payload["table"], ensure_ascii=False)
                                            if analysis_payload.get("table") else None),
                                chart_json=(json.dumps(analysis_payload["chart"], ensure_ascii=False)
                                            if analysis_payload.get("chart") else None),
                                error_msg=analysis_payload.get("error"),
                                status="error" if analysis_payload.get("error") else "ok",
                            ))
                        db.commit()
                except Exception as e:
                    print("chat persist failed:", type(e).__name__, e, file=sys.stderr)

    return StreamingResponse(generate(), media_type="text/event-stream")


# ———————— 会话历史持久化接口 ————————


@app.get("/sessions")
def list_sessions(user: User = Depends(get_current_user)):
    """返回当前用户会话列表（按更新时间倒序）"""
    with get_session() as db:
        rows = (db.query(DbSession)
                .filter(DbSession.user_id == user.id)
                .order_by(DbSession.updated_at.desc())
                .all())
        out = []
        for s in rows:
            msg_count = db.query(ChatMessage).filter(ChatMessage.session_id == s.session_id).count()
            out.append({
                "session_id": s.session_id,
                "title": s.title or "新会话",
                "mode": s.mode,
                "updated_at": s.updated_at.isoformat(timespec="seconds") if s.updated_at else "",
                "message_count": msg_count,
            })
        return out


@app.post("/sessions")
def create_session(payload: dict = None, user: User = Depends(get_current_user)):
    """新建空会话，返回 session_id"""
    mode = (payload or {}).get("mode", "chat")
    session_id = uuid.uuid4().hex
    with get_session() as db:
        db.add(DbSession(session_id=session_id, user_id=user.id,
                         mode=mode, title=None))
        db.commit()
    return {"session_id": session_id, "title": "新会话", "mode": mode}


@app.patch("/sessions/{session_id}")
def rename_session(session_id: str, payload: dict, user: User = Depends(get_current_user)):
    """重命名会话（仅限本人会话）"""
    with get_session() as db:
        s = db.get(DbSession, session_id)
        if s is None or s.user_id != user.id:
            raise HTTPException(status_code=404, detail="会话不存在或无权访问")
        title = (payload.get("title") or "").strip()
        if not title:
            raise HTTPException(status_code=400, detail="标题不能为空")
        s.title = title[:100]
        s.updated_at = datetime.now()
        db.commit()
        # 在 session 关闭前提取 title，避免 detached ORM 对象触发懒加载异常
        new_title = s.title
    return {"session_id": session_id, "title": new_title}


@app.delete("/sessions/{session_id}")
def delete_session(session_id: str, request: Request, user: User = Depends(get_current_user)):
    """删除会话（仅限本人会话）"""
    with get_session() as db:
        s = db.get(DbSession, session_id)
        if s is None or s.user_id != user.id:
            raise HTTPException(status_code=404, detail="会话不存在或无权访问")
        # 先删子表（ON DELETE CASCADE 应已生效，但显式删更稳）
        db.query(ChatMessage).filter(ChatMessage.session_id == session_id).delete()
        db.query(AnalysisResult).filter(AnalysisResult.session_id == session_id).delete()
        db.delete(s)
        db.commit()
    auth.log_action(user.id, "delete_session", "session", session_id,
                    ip=auth._client_ip(request))
    return {"ok": True}


@app.get("/sessions/{session_id}/messages")
def get_session_messages(session_id: str, user: User = Depends(get_current_user)):
    """返回会话消息历史（role、content）"""
    with get_session() as db:
        s = db.get(DbSession, session_id)
        if s is None or s.user_id != user.id:
            raise HTTPException(status_code=404, detail="会话不存在或无权访问")
        msgs = (db.query(ChatMessage)
                .filter(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.id.asc())
                .all())
        return {
            "session_id": session_id,
            "title": s.title or "新会话",
            "mode": s.mode,
            "messages": [{"role": m.role, "content": m.content} for m in msgs],
        }


@app.get("/")
def index():
    # 根路由不鉴权：前端在加载时调 /auth/me 判断登录态
    # 加 no-cache 头：避免浏览器缓存旧版前端，每次启动都拉最新 HTML
    return FileResponse(
        os.path.join(BASE_DIR, "static", "index.html"),
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


# 启动时确保默认 admin 用户存在（用 .env 的 ADMIN_PASSWORD 生成 bcrypt 哈希）
ensure_default_user()
