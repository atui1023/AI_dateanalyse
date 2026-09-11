import json
import os
import re
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd
import psutil
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from starlette.middleware.sessions import SessionMiddleware

import auth
import kb
import llm
from auth import get_current_user
# 审计日志通过 auth.log_action 调用（用模块前缀更清晰，避免和本地变量混淆）
from db import (
    AnalysisComment, AnalysisRelation, AnalysisResult, AnalysisShare, Base,
    ChatMessage, Dashboard, DashboardItem, ScheduleJob, ScheduleRun,
    Session as DbSession, User, engine,
    ensure_compat_schema, ensure_default_user, get_session,
)

app = FastAPI(title="AI 数据分析")

# CORS：开发期 Vue dev server (5173) 直连后端 (8000) 走 fetch，需要跨域
# 生产环境构建产物由 FastAPI 托管，同源，不触发 CORS
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# SessionMiddleware：cookie 签名存储 user_id，30 天有效期
# secret_key 从 .env 读取，缺失时用固定串（开发环境兜底，生产必须配置）
# same_site=lax：前端请求全部同源（开发期走 Vite proxy 5173，生产期 FastAPI 托管），
# Lax 下同源请求自动带 cookie，且不要求 Secure（http 开发环境可用）。
# 不能用 same_site="none" + https_only=False：Chrome 会拒收「SameSite=None 但无 Secure」
# 的 Set-Cookie，表现为登录 cookie 能用、登出时的删除 cookie 被丢弃（session 无法失效）。
_SESSION_SECRET = os.getenv("SESSION_SECRET")
if not _SESSION_SECRET:
    raise RuntimeError("未设置 SESSION_SECRET，请在 .env 中设置随机密钥")
app.add_middleware(
    SessionMiddleware,
    secret_key=_SESSION_SECRET,
    session_cookie="session",
    max_age=30 * 24 * 3600,  # 30 天
    same_site="lax",
    https_only=False,  # 开发期 http；生产由反向代理终止 https 时回源也是 http
    path="/",
)

# 注册鉴权路由（/auth/login / /auth/register / /auth/logout / /auth/me）
app.include_router(auth.router)

BASE_DIR = os.path.dirname(__file__)
UPLOAD_DIR = os.path.join(BASE_DIR, "uploads")
KB_DIR = os.path.join(UPLOAD_DIR, "kb")
FRONTEND_DIST_DIR = os.path.join(BASE_DIR, "frontend", "dist")
os.makedirs(KB_DIR, exist_ok=True)

# 生产/桌面启动统一托管 Vue 构建产物。
if os.path.isdir(os.path.join(FRONTEND_DIST_DIR, "assets")):
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIST_DIR, "assets")), name="frontend-assets")
    app.mount("/favicon.svg", StaticFiles(directory=FRONTEND_DIST_DIR), name="frontend-favicon")

# 内存中保存已挂载数据集的信息，按用户严格隔离：
#   user_id -> { doc_id -> {path, ext, filename, summary, doc_id} }
# 用户只能看到/分析自己挂载的数据集，互不可见。
datasets: Dict[int, Dict[str, dict]] = {}


def user_datasets(user_id: int) -> Dict[str, dict]:
    """返回指定用户的挂载 dict（不存在则初始化）"""
    return datasets.setdefault(user_id, {})


def mount_dataset(user_id: int, doc_id: str, info: dict) -> None:
    """为用户挂载（或刷新）一个数据集"""
    user_datasets(user_id)[doc_id] = info


def unmount_dataset(user_id: int, doc_id: str) -> None:
    """卸载指定用户的某个数据集（不存在不报错）"""
    user_datasets(user_id).pop(doc_id, None)


def get_active_datasets(user_id: int, doc_ids: List[str]) -> List[dict]:
    """按前端传入顺序收集当前用户挂载的数据集（对应 df1、df2……）。
    非本人挂载的 doc_id 直接忽略，杜绝跨用户访问他人数据。"""
    mine = user_datasets(user_id)
    return [mine[i] for i in doc_ids if i in mine]


def get_owned_session(db, session_id: str, user: User) -> DbSession:
    """统一会话归属校验：不存在或不属于当前用户一律 404（不暴露会话是否存在）。
    所有会话相关路由必须经此函数取会话，防止漏口导致跨用户访问。"""
    s = db.get(DbSession, session_id)
    if s is None or s.user_id != user.id:
        raise HTTPException(status_code=404, detail="会话不存在或无权访问")
    return s


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
    missing_by_column = {
        str(col): int(df[col].isna().sum())
        for col in df.columns
        if int(df[col].isna().sum()) > 0
    }
    duplicate_rows = int(df.duplicated().sum())
    empty_rows = int(df.isna().all(axis=1).sum()) if len(df.columns) else int(len(df))
    quality_issues = []
    if missing_by_column:
        quality_issues.append({
            "level": "warning",
            "code": "missing_values",
            "message": f"{len(missing_by_column)} 个字段存在缺失值，共 {sum(missing_by_column.values())} 个",
        })
    if duplicate_rows:
        quality_issues.append({
            "level": "warning",
            "code": "duplicate_rows",
            "message": f"发现 {duplicate_rows} 行重复数据",
        })
    if empty_rows:
        quality_issues.append({
            "level": "warning",
            "code": "empty_rows",
            "message": f"发现 {empty_rows} 行完全为空的数据",
        })
    duplicated_columns = [str(c) for c in df.columns[df.columns.duplicated()].tolist()]
    if duplicated_columns:
        quality_issues.append({
            "level": "error",
            "code": "duplicate_columns",
            "message": f"字段名重复：{'、'.join(duplicated_columns)}",
        })
    return {
        "rows": int(df.shape[0]),
        "cols": int(df.shape[1]),
        "columns": columns,
        "preview_columns": [str(c) for c in df.columns],
        "preview_rows": preview,
        "quality": {
            "missing_cells": int(df.isna().sum().sum()),
            "missing_rate": round(float(df.isna().sum().sum()) / max(df.size, 1), 4),
            "duplicate_rows": duplicate_rows,
            "empty_rows": empty_rows,
            "missing_by_column": missing_by_column,
            "issues": quality_issues,
        },
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
    mount_dataset(user.id, doc["doc_id"], {
        "path": save_path,
        "ext": ext,
        "filename": filename,
        "summary": summary,
        "doc_id": doc["doc_id"],
    })
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
    unmount_dataset(user.id, dataset_id)
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
    # 挂载数量上限按用户计数
    if doc_id not in user_datasets(user.id) and len(user_datasets(user.id)) >= MAX_DATASETS:
        raise HTTPException(status_code=400, detail=f"最多同时挂载 {MAX_DATASETS} 个数据集，请先卸载部分文件")
    mount_dataset(user.id, doc_id, {
        "path": rec["path"],
        "ext": rec["ext"],
        "filename": rec["filename"],
        "summary": summary,
        "doc_id": doc_id,
    })
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

    summary = None
    if ext in ALLOWED_EXT:
        try:
            summary = build_summary(load_dataframe(save_path, ext))
        except Exception as e:
            if os.path.exists(save_path):
                os.remove(save_path)
            raise HTTPException(status_code=400, detail=f"文件解析失败：{e}")

    doc = kb.register_document(save_path, ext, filename, folder_id=folder_id, user_id=user.id)
    auth.log_action(user.id, "upload_doc", "document", doc["doc_id"],
                    detail=f"upload {filename} ({ext})", ip=auth._client_ip(request))
    return {"doc_id": doc["doc_id"], "filename": doc["filename"], "status": doc["status"], "summary": summary}


@app.get("/kb/documents")
def kb_documents(user: User = Depends(get_current_user)):
    """列出知识库中的全部文档（含解析状态：parsing/ready/failed）"""
    return kb.list_documents(user_id=user.id)


@app.delete("/kb/documents/{doc_id}")
def kb_delete(doc_id: str, request: Request, user: User = Depends(get_current_user)):
    """删除知识库文档：同步清理向量块/向量（防幽灵数据）+ 注册表记录 + 磁盘文件 + 分析挂载"""
    rec = kb.delete_document(doc_id, user_id=user.id)
    if rec is None:
        # 文档不存在或不属于当前用户：统一 404，不泄露资源是否存在
        raise HTTPException(status_code=404, detail="文档不存在或无权访问")
    unmount_dataset(user.id, doc_id)  # 若正挂载为 df，一并取消挂载（仅本人命名空间）
    if rec and rec.get("path") and os.path.exists(rec["path"]):
        try:
            os.remove(rec["path"])
        except OSError:
            pass
    auth.log_action(user.id, "delete_doc", "document", doc_id,
                    detail=f"delete {rec.get('filename', '') if rec else ''}",
                    ip=auth._client_ip(request))
    return {"ok": True}


@app.post("/kb/documents/{doc_id}/version")
async def kb_upload_version(doc_id: str, request: Request, file: UploadFile = File(...), user: User = Depends(get_current_user)):
    """上传文档新版本，保留旧版本并重新解析向量。"""
    filename = os.path.basename(file.filename or "未命名文档")
    ext = os.path.splitext(filename)[1].lower()
    if ext not in kb.KB_EXTENSIONS:
        raise HTTPException(status_code=400, detail="仅支持 TXT / MD / PDF / CSV / Excel 文档")
    content = await file.read()
    if len(content) > kb.MAX_KB_FILE_SIZE:
        raise HTTPException(status_code=400, detail="文档大小不能超过 20MB")
    save_path = os.path.join(KB_DIR, f"{uuid.uuid4().hex}{ext}")
    with open(save_path, "wb") as f:
        f.write(content)
    try:
        doc = kb.replace_document(doc_id, save_path, ext, filename, len(content), user_id=user.id)
    except ValueError as e:
        if os.path.exists(save_path):
            os.remove(save_path)
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        if os.path.exists(save_path):
            os.remove(save_path)
        raise HTTPException(status_code=400, detail=f"上传新版本失败：{e}")
    mounted = user_datasets(user.id).get(doc_id)
    if mounted and ext in ALLOWED_EXT:
        try:
            summary = build_summary(load_dataframe(save_path, ext))
            mounted.update({"path": save_path, "ext": ext, "filename": filename, "summary": summary})
        except Exception:
            pass
    elif mounted:
        # 版本替换为文档类文件后，原来的 df 挂载已经失效，必须同步卸载。
        unmount_dataset(user.id, doc_id)
    auth.log_action(user.id, "upload_doc_version", "document", doc_id,
                    detail=f"upload version {doc.get('version')}: {filename}",
                    ip=auth._client_ip(request))
    return {"doc_id": doc_id, "filename": filename, "version": doc.get("version"), "status": doc.get("status")}


@app.get("/kb/documents/{doc_id}/versions")
def kb_document_versions(doc_id: str, user: User = Depends(get_current_user)):
    try:
        kb.get_document(doc_id, user_id=user.id)
        return kb.list_document_versions(doc_id, user_id=user.id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


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


@app.patch("/kb/documents/{doc_id}/metadata")
def kb_doc_metadata(doc_id: str, payload: dict, user: User = Depends(get_current_user)):
    """更新文档元数据：{"tags": ["销售"], "favorite": true}"""
    tags = payload.get("tags")
    if tags is not None and not isinstance(tags, list):
        raise HTTPException(status_code=400, detail="tags 必须是数组")
    try:
        return kb.update_doc_metadata(
            doc_id, tags=tags, favorite=payload.get("favorite"), user_id=user.id
        )
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


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
    """在受监控的独立子进程中执行模型生成的 pandas 代码。

    active_datasets 按顺序对应代码环境中的 df1、df2……
    """
    code_path = os.path.join(UPLOAD_DIR, f"code_{uuid.uuid4().hex}.py")
    with open(code_path, "w", encoding="utf-8") as f:
        f.write(code)

    manifest = json.dumps(
        [{"path": d["path"], "ext": d["ext"]} for d in active_datasets],
        ensure_ascii=False,
    )

    timeout_seconds = int(os.getenv("ANALYSIS_TIMEOUT_SECONDS", "30"))
    memory_limit = int(os.getenv("ANALYSIS_MEMORY_LIMIT_MB", "1536")) * 1024 * 1024
    proc = None
    limit_error = None
    output = ""
    process_stderr = ""
    stdout_path = ""
    stderr_path = ""
    try:
        with tempfile.TemporaryDirectory(prefix="analysis_runner_") as temp_dir:
            stdout_path = os.path.join(temp_dir, "stdout.log")
            stderr_path = os.path.join(temp_dir, "stderr.log")
            with open(stdout_path, "w", encoding="utf-8") as stdout_file, \
                    open(stderr_path, "w", encoding="utf-8") as stderr_file:
                proc = subprocess.Popen(
                    [sys.executable, os.path.join(BASE_DIR, "runner.py"), code_path, manifest],
                    stdout=stdout_file,
                    stderr=stderr_file,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    env={**os.environ, "PYTHONIOENCODING": "utf-8", "PYTHONUTF8": "1"},
                    creationflags=(subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0),
                )
                monitored = psutil.Process(proc.pid)
                started_at = time.monotonic()
                while proc.poll() is None:
                    if time.monotonic() - started_at > timeout_seconds:
                        limit_error = f"代码执行超时（超过 {timeout_seconds} 秒），请简化分析逻辑后重试"
                        break
                    try:
                        processes = [monitored, *monitored.children(recursive=True)]
                        memory_used = sum(item.memory_info().rss for item in processes if item.is_running())
                        if memory_used > memory_limit:
                            limit_error = f"代码执行内存超限（超过 {memory_limit // 1024 // 1024} MB）"
                            break
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                    time.sleep(0.08)
                if limit_error:
                    try:
                        descendants = monitored.children(recursive=True)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        descendants = []
                    for item in descendants:
                        try:
                            item.kill()
                        except (psutil.NoSuchProcess, psutil.AccessDenied):
                            pass
                    try:
                        monitored.kill()
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        pass
                    proc.wait(timeout=3)
            with open(stdout_path, "r", encoding="utf-8", errors="replace") as output_file:
                output = output_file.read()
            with open(stderr_path, "r", encoding="utf-8", errors="replace") as error_file:
                process_stderr = error_file.read()
    except OSError as exc:
        return {"error": f"代码执行进程启动失败：{exc}"}
    finally:
        if os.path.exists(code_path):
            os.remove(code_path)

    if limit_error:
        return {"error": limit_error}
    if proc is None:
        return {"error": "代码执行进程启动失败"}
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
    err_tail = process_stderr.strip()
    out_tail = output.strip()[-200:]
    detail = err_tail or out_tail or "进程无任何输出"
    return {"error": f"代码执行失败（退出码 {proc.returncode}）：{detail[:300]}"}



def run_analysis_with_retries(active_datasets: List[dict], question: str, code: str) -> dict:
    """Execute analysis code and ask the model to repair it at most twice."""
    current_code = code
    last_result: dict = {"error": "代码执行失败"}
    for attempt in range(3):
        result = run_analysis(active_datasets, current_code)
        if not result.get("error"):
            return {**result, "retry_count": attempt, "code": current_code}
        last_result = result
        if attempt >= 2:
            break
        try:
            repaired = llm.repair_analysis_code(question, current_code, result["error"])
        except llm.ModelConnectionError:
            break
        if not repaired or repaired == current_code:
            break
        current_code = repaired
    return {**last_result, "retry_count": min(2, attempt), "code": current_code}


def dataset_snapshot(active_datasets: List[dict]) -> list:
    """保存本次分析实际使用的数据集元数据，避免文件后续变化导致记录失真。"""
    return [
        {
            "dataset_id": ds.get("doc_id"),
            "filename": ds.get("filename"),
            "rows": ds.get("summary", {}).get("rows", 0),
            "cols": ds.get("summary", {}).get("cols", 0),
            "columns": ds.get("summary", {}).get("preview_columns", []),
        }
        for ds in active_datasets
    ]


def save_analysis_result(user_id: int, session_id: str, question: str, payload: dict) -> int:
    """在分析执行完成后立即保存，避免客户端中断流式响应导致记录丢失。"""
    with get_session() as db:
        row = AnalysisResult(
            user_id=user_id,
            session_id=session_id,
            question=question,
            code=payload.get("code"),
            stdout=payload.get("stdout"),
            table_json=(json.dumps(payload["table"], ensure_ascii=False) if payload.get("table") else None),
            chart_json=(json.dumps(payload["chart"], ensure_ascii=False) if payload.get("chart") else None),
            dataset_json=json.dumps(payload.get("datasets", []), ensure_ascii=False),
            conclusion=payload.get("stdout") or None,
            execution_ms=payload.get("execution_ms"),
            error_msg=payload.get("error"),
            status="error" if payload.get("error") else "ok",
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return row.id

def sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.post("/chat")
def chat(req: ChatRequest, user: User = Depends(get_current_user)):
    """流式对话接口：SSE 逐字返回模型回复。

    三种场景：数据分析（挂载数据集）/ RAG 知识库问答（mode=rag）/ 普通聊天
    持久化：用户消息先落库，流式结束追加 assistant 消息 + 分析结果
    """
    # 按前端传入顺序收集"当前用户"挂载的数据集（对应 df1、df2……）
    # 非本人挂载的 doc_id 一律忽略，防止跨用户分析他人数据
    active = get_active_datasets(user.id, req.dataset_ids)

    # —— 会话归属校验 / 新建会话 ——
    session_id = req.session_id
    with get_session() as db:
        if session_id:
            get_owned_session(db, session_id, user)
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
    elif req.mode == "analysis":
        # 数据分析模式：挂载数据集 + 知识库业务规则 双路并行
        summary_text = "\n\n".join(dataset_summary_text(i + 1, ds) for i, ds in enumerate(active)) if active else ""
        # 分析时也参考永久知识库中的业务规则（Top-3）；库为空或检索失败都不影响分析
        kb_context = ""
        try:
            if question and kb.doc_count(user_id=user.id) > 0:
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
                    started_at = time.perf_counter()
                    result = run_analysis_with_retries(active, question, code)
                    execution_ms = int((time.perf_counter() - started_at) * 1000)
                    analysis_payload = {
                        "code": result.pop("code", None) or code,
                        "execution_ms": execution_ms,
                        "datasets": dataset_snapshot(active),
                        **result,
                    }
                    result_id = save_analysis_result(_uid, _sid, question, analysis_payload)
                    analysis_payload["result_id"] = result_id
                    yield sse({"type": "result", "result_id": result_id, **result})
                else:
                    analysis_payload = {
                        "code": None,
                        "execution_ms": 0,
                        "datasets": dataset_snapshot(active),
                        "error": "模型没有生成可执行的分析代码，请换个问法试试",
                    }
                    result_id = save_analysis_result(_uid, _sid, question, analysis_payload)
                    yield sse({"type": "result", "result_id": result_id, "error": analysis_payload["error"]})

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
                        db.commit()
                except Exception as e:
                    print("chat persist failed:", type(e).__name__, e, file=sys.stderr)

    return StreamingResponse(generate(), media_type="text/event-stream")




# ---------------- 工作台：关联分析、仪表盘、分享协作、定时任务 ----------------
def _json_value(raw, default):
    if not raw:
        return default
    try:
        return json.loads(raw)
    except (TypeError, json.JSONDecodeError):
        return default


def _result_view(row: AnalysisResult) -> dict:
    return {
        "id": row.id,
        "question": row.question,
        "status": row.status,
        "stdout": row.stdout,
        "table": _json_value(row.table_json, None),
        "chart": _json_value(row.chart_json, None),
        "datasets": _json_value(row.dataset_json, []),
        "execution_ms": row.execution_ms,
        "created_at": row.created_at.isoformat(timespec="seconds") if row.created_at else "",
    }


def _owned_result(db, result_id: int, user: User) -> AnalysisResult:
    row = db.query(AnalysisResult).filter(
        AnalysisResult.id == result_id, AnalysisResult.user_id == user.id
    ).first()
    if row is None:
        raise HTTPException(status_code=404, detail="分析结果不存在或无权访问")
    return row


def _table_from_df(frame: pd.DataFrame) -> dict:
    safe = frame.head(200).copy().where(pd.notna(frame), "")
    return {
        "columns": [str(c) for c in safe.columns],
        "rows": safe.astype(object).values.tolist(),
        "truncated": len(frame.index) > 200,
    }


@app.get("/analysis/results")
def analysis_results(limit: int = 50, user: User = Depends(get_current_user)):
    with get_session() as db:
        rows = (db.query(AnalysisResult)
                .filter(AnalysisResult.user_id == user.id)
                .order_by(AnalysisResult.created_at.desc())
                .limit(max(1, min(limit, 200))).all())
        return [_result_view(row) for row in rows]


@app.get("/analysis/relations")
def list_analysis_relations(user: User = Depends(get_current_user)):
    with get_session() as db:
        rows = db.query(AnalysisRelation).filter(
            AnalysisRelation.user_id == user.id
        ).order_by(AnalysisRelation.updated_at.desc()).all()
        return [{
            "id": row.id, "name": row.name,
            "dataset_ids": _json_value(row.dataset_ids_json, []),
            "joins": _json_value(row.joins_json, []),
            "updated_at": row.updated_at.isoformat(timespec="seconds") if row.updated_at else "",
        } for row in rows]


@app.post("/analysis/relations")
def create_analysis_relation(payload: dict, user: User = Depends(get_current_user)):
    name = str(payload.get("name") or "未命名关联").strip()[:128]
    dataset_ids = payload.get("dataset_ids") or []
    joins = payload.get("joins") or []
    if not isinstance(dataset_ids, list) or len(dataset_ids) < 2:
        raise HTTPException(status_code=400, detail="至少选择两个数据集")
    if not isinstance(joins, list) or len(joins) < 1:
        raise HTTPException(status_code=400, detail="请配置至少一个关联条件")
    for join in joins:
        if not all(join.get(key) for key in ("left_dataset_id", "right_dataset_id", "left_key", "right_key")):
            raise HTTPException(status_code=400, detail="关联条件不完整")
    row = AnalysisRelation(
        id=uuid.uuid4().hex, user_id=user.id, name=name,
        dataset_ids_json=json.dumps(dataset_ids, ensure_ascii=False),
        joins_json=json.dumps(joins, ensure_ascii=False),
    )
    with get_session() as db:
        db.add(row)
        db.commit()
    return {"id": row.id, "name": row.name, "dataset_ids": dataset_ids, "joins": joins}


@app.delete("/analysis/relations/{relation_id}")
def delete_analysis_relation(relation_id: str, user: User = Depends(get_current_user)):
    with get_session() as db:
        row = db.query(AnalysisRelation).filter(
            AnalysisRelation.id == relation_id, AnalysisRelation.user_id == user.id
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail="关联配置不存在")
        db.delete(row)
        db.commit()
    return {"ok": True}


@app.post("/analysis/relations/{relation_id}/execute")
def execute_analysis_relation(relation_id: str, payload: dict, user: User = Depends(get_current_user)):
    """执行关联并落地为新的可读取 CSV 数据集，同时按用户指令生成分析结果。"""
    instruction = str(payload.get("instruction") or "").strip()
    with get_session() as db:
        relation = db.query(AnalysisRelation).filter(AnalysisRelation.id == relation_id, AnalysisRelation.user_id == user.id).first()
        if relation is None:
            raise HTTPException(status_code=404, detail="关联配置不存在")
        dataset_ids = _json_value(relation.dataset_ids_json, [])
        joins = _json_value(relation.joins_json, [])
    frames = {}
    try:
        for doc_id in dataset_ids:
            doc = kb.get_document(doc_id, user_id=user.id)
            if doc.get("ext") not in ALLOWED_EXT:
                raise ValueError(f"{doc.get('filename')} 不是可关联的表格文件")
            frames[doc_id] = load_dataframe(doc["path"], doc["ext"])
        merged = frames[dataset_ids[0]]
        for join in joins:
            merged = merged.merge(frames[join["right_dataset_id"]], left_on=join["left_key"], right_on=join["right_key"],
                                  how=join.get("how", "left"), suffixes=("", "_关联"))
    except (ValueError, KeyError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=f"关联执行失败：{exc}")
    output_path = os.path.join(KB_DIR, f"关联结果_{uuid.uuid4().hex}.csv")
    merged.to_csv(output_path, index=False, encoding="utf-8-sig")
    doc = kb.register_document(output_path, ".csv", f"{relation.name}_关联结果.csv", user_id=user.id)
    summary = build_summary(merged)
    result_id = None
    result = {"table": _table_from_df(merged), "chart": None, "stdout": "关联结果文件已生成，可继续在数据分析或仪表盘中读取。", "error": None}
    if instruction:
        mount = {"doc_id": doc["doc_id"], "path": output_path, "ext": ".csv", "filename": doc["filename"], "summary": summary}
        try:
            response = llm.chat_model.invoke([{"role": "system", "content": llm.build_system_prompt(dataset_summary_text(1, mount))}, {"role": "user", "content": instruction}])
            generated = response.content if hasattr(response, "content") else str(response)
            code = extract_code(generated)
            if code:
                executed = run_analysis([mount], code)
                result.update(executed)
                result["code"] = code
            else:
                result["stdout"] = generated
        except Exception as exc:
            result["error"] = f"自定义指令执行失败：{exc}"
        result_id = save_analysis_result(user.id, None, instruction, {**result, "code": result.get("code"), "datasets": [dataset_snapshot([mount])[0]], "execution_ms": 0})
    return {"doc_id": doc["doc_id"], "filename": doc["filename"], "summary": summary, "result_id": result_id, "result": {**result, "result_id": result_id}}


@app.post("/analysis/relations/{relation_id}/preview")
def preview_analysis_relation(relation_id: str, user: User = Depends(get_current_user)):
    with get_session() as db:
        row = db.query(AnalysisRelation).filter(
            AnalysisRelation.id == relation_id, AnalysisRelation.user_id == user.id
        ).first()
        if row is None:
            raise HTTPException(status_code=404, detail="关联配置不存在")
        dataset_ids = _json_value(row.dataset_ids_json, [])
        joins = _json_value(row.joins_json, [])
    frames = {}
    try:
        for doc_id in dataset_ids:
            doc = kb.get_document(doc_id, user_id=user.id)
            if doc.get("ext") not in ALLOWED_EXT:
                raise ValueError(f"{doc.get('filename')} 不是可关联的表格文件")
            frames[doc_id] = load_dataframe(doc["path"], doc["ext"])
        frame = frames[dataset_ids[0]]
        for join in joins:
            right_id = join["right_dataset_id"]
            if right_id not in frames:
                raise ValueError("关联条件引用了未选择的数据集")
            frame = frame.merge(
                frames[right_id],
                left_on=join["left_key"], right_on=join["right_key"],
                how=join.get("how", "left"), suffixes=("", f"_{right_id[:6]}"),
            )
    except (ValueError, KeyError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=f"关联预览失败：{exc}")
    return {"relation_id": relation_id, "name": row.name, "rows": len(frame), "table": _table_from_df(frame)}


@app.post("/dashboards/{dashboard_id}/analyze")
def analyze_dashboard(dashboard_id: str, payload: dict, user: User = Depends(get_current_user)):
    instruction = str(payload.get("instruction") or "").strip()
    dataset_ids = payload.get("dataset_ids") or []
    if not instruction or not isinstance(dataset_ids, list) or not dataset_ids:
        raise HTTPException(status_code=400, detail="请选择文件并填写自定义指令")
    with get_session() as db:
        board = db.query(Dashboard).filter(Dashboard.id == dashboard_id, Dashboard.user_id == user.id).first()
        if board is None:
            raise HTTPException(status_code=404, detail="仪表盘不存在")
    mounts = []
    try:
        for doc_id in dataset_ids:
            doc = kb.get_document(doc_id, user_id=user.id)
            if doc.get("ext") not in ALLOWED_EXT:
                raise ValueError(f"{doc.get('filename')} 不是表格文件")
            mounts.append({"doc_id": doc_id, "path": doc["path"], "ext": doc["ext"], "filename": doc["filename"],
                           "summary": build_summary(load_dataframe(doc["path"], doc["ext"]))})
    except (ValueError, KeyError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=f"读取仪表盘文件失败：{exc}")
    try:
        summary = "\n\n".join(dataset_summary_text(i + 1, item) for i, item in enumerate(mounts))
        response = llm.chat_model.invoke([
            {"role": "system", "content": llm.build_system_prompt(summary)},
            {"role": "user", "content": instruction},
        ])
        generated = response.content if hasattr(response, "content") else str(response)
        code = extract_code(generated)
        if not code:
            result = {"stdout": generated, "table": None, "chart": None, "error": None, "code": None}
        else:
            result = run_analysis_with_retries(mounts, instruction, code)
    except llm.ModelConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        detail = str(exc)
        if "quota" in detail.lower() or "FreeTierOnly" in detail:
            raise HTTPException(status_code=503, detail="模型额度已用完，暂时无法执行自定义指令；请检查模型账户额度")
        raise HTTPException(status_code=503, detail=f"模型服务暂时不可用：{detail[:200]}")
    result_id = save_analysis_result(user.id, None, instruction, {
        **result, "datasets": dataset_snapshot(mounts), "execution_ms": 0,
    })
    if not result_id:
        raise HTTPException(status_code=500, detail="分析结果保存失败")
    with get_session() as db:
        item = DashboardItem(
            id=uuid.uuid4().hex, dashboard_id=dashboard_id, result_id=result_id,
            title=instruction[:128], chart_config_json=json.dumps({"type": "original"}), position_json="{}",
        )
        db.add(item)
        board = db.query(Dashboard).filter(Dashboard.id == dashboard_id, Dashboard.user_id == user.id).first()
        if board:
            board.updated_at = datetime.now()
        db.commit()
    return {"result": {"result_id": result_id, **result}, "dashboard": get_dashboard(dashboard_id, user)}


@app.post("/dashboards")
def create_dashboard(payload: dict, user: User = Depends(get_current_user)):
    name = str(payload.get("name") or "新仪表盘").strip()[:128]
    row = Dashboard(id=uuid.uuid4().hex, user_id=user.id, name=name,
                    description=str(payload.get("description") or "")[:500])
    result = {"id": row.id, "name": row.name, "description": row.description, "items": []}
    with get_session() as db:
        db.add(row)
        db.commit()
    return result


@app.get("/dashboards")
def list_dashboards(user: User = Depends(get_current_user)):
    with get_session() as db:
        rows = db.query(Dashboard).filter(Dashboard.user_id == user.id).order_by(Dashboard.updated_at.desc()).all()
        result = []
        for row in rows:
            count = db.query(DashboardItem).filter(DashboardItem.dashboard_id == row.id).count()
            result.append({"id": row.id, "name": row.name, "description": row.description, "item_count": count,
                           "updated_at": row.updated_at.isoformat(timespec="seconds") if row.updated_at else ""})
        return result


@app.get("/dashboards/{dashboard_id}")
def get_dashboard(dashboard_id: str, user: User = Depends(get_current_user)):
    with get_session() as db:
        board = db.query(Dashboard).filter(Dashboard.id == dashboard_id, Dashboard.user_id == user.id).first()
        if board is None:
            raise HTTPException(status_code=404, detail="仪表盘不存在")
        items = db.query(DashboardItem).filter(DashboardItem.dashboard_id == board.id).order_by(DashboardItem.created_at.asc()).all()
        result_ids = [item.result_id for item in items]
        rows = db.query(AnalysisResult).filter(AnalysisResult.id.in_(result_ids), AnalysisResult.user_id == user.id).all() if result_ids else []
        result_map = {row.id: _result_view(row) for row in rows}
        return {"id": board.id, "name": board.name, "description": board.description,
                "items": [{"id": item.id, "title": item.title,
                           "chart_config": _json_value(item.chart_config_json, {}),
                           "position": _json_value(item.position_json, {}),
                           "result": result_map.get(item.result_id)} for item in items]}


@app.post("/dashboards/{dashboard_id}/items")
def add_dashboard_item(dashboard_id: str, payload: dict, user: User = Depends(get_current_user)):
    result_id = payload.get("result_id")
    if not result_id:
        raise HTTPException(status_code=400, detail="请选择分析结果")
    with get_session() as db:
        board = db.query(Dashboard).filter(Dashboard.id == dashboard_id, Dashboard.user_id == user.id).first()
        _owned_result(db, int(result_id), user)
        if board is None:
            raise HTTPException(status_code=404, detail="仪表盘不存在")
        item = DashboardItem(id=uuid.uuid4().hex, dashboard_id=dashboard_id, result_id=int(result_id),
                             title=str(payload.get("title") or "分析图表")[:128],
                             chart_config_json=json.dumps(payload.get("chart_config") or {}, ensure_ascii=False),
                             position_json=json.dumps(payload.get("position") or {}, ensure_ascii=False))
        db.add(item)
        board.updated_at = datetime.now()
        db.commit()
        return {"id": item.id, "dashboard_id": dashboard_id}


@app.delete("/dashboards/{dashboard_id}")
def delete_dashboard(dashboard_id: str, user: User = Depends(get_current_user)):
    with get_session() as db:
        board = db.query(Dashboard).filter(Dashboard.id == dashboard_id, Dashboard.user_id == user.id).first()
        if board is None:
            raise HTTPException(status_code=404, detail="仪表盘不存在")
        db.delete(board)
        db.commit()
    return {"ok": True}


@app.delete("/dashboards/{dashboard_id}/items/{item_id}")
def delete_dashboard_item(dashboard_id: str, item_id: str, user: User = Depends(get_current_user)):
    with get_session() as db:
        board = db.query(Dashboard).filter(Dashboard.id == dashboard_id, Dashboard.user_id == user.id).first()
        item = db.query(DashboardItem).filter(DashboardItem.id == item_id, DashboardItem.dashboard_id == dashboard_id).first()
        if board is None or item is None:
            raise HTTPException(status_code=404, detail="仪表盘项目不存在")
        db.delete(item)
        board.updated_at = datetime.now()
        db.commit()
    return {"ok": True}


@app.post("/shares")
def create_share(payload: dict, user: User = Depends(get_current_user)):
    result_id = payload.get("result_id")
    dashboard_id = payload.get("dashboard_id")
    with get_session() as db:
        if result_id:
            _owned_result(db, int(result_id), user)
        if dashboard_id and not db.query(Dashboard).filter(Dashboard.id == dashboard_id, Dashboard.user_id == user.id).first():
            raise HTTPException(status_code=404, detail="仪表盘不存在")
        if not result_id and not dashboard_id:
            raise HTTPException(status_code=400, detail="请选择分析结果或仪表盘")
        token = uuid.uuid4().hex
        db.add(AnalysisShare(token=token, user_id=user.id, result_id=int(result_id) if result_id else None,
                             dashboard_id=dashboard_id, allow_comments=True))
        db.commit()
    return {"token": token, "path": f"/shared/{token}"}


def _public_share_payload(db, share: AnalysisShare) -> dict:
    payload = {"token": share.token, "allow_comments": share.allow_comments,
               "created_at": share.created_at.isoformat(timespec="seconds") if share.created_at else ""}
    if share.result_id:
        row = db.get(AnalysisResult, share.result_id)
        payload["result"] = _result_view(row) if row else None
    if share.dashboard_id:
        board = db.get(Dashboard, share.dashboard_id)
        payload["dashboard"] = {"id": board.id, "name": board.name, "description": board.description} if board else None
        items = db.query(DashboardItem).filter(DashboardItem.dashboard_id == share.dashboard_id).all()
        result_ids = [item.result_id for item in items]
        rows = db.query(AnalysisResult).filter(AnalysisResult.id.in_(result_ids)).all() if result_ids else []
        result_map = {row.id: _result_view(row) for row in rows}
        payload["items"] = [{"id": item.id, "title": item.title, "chart_config": _json_value(item.chart_config_json, {}),
                             "result": result_map.get(item.result_id)} for item in items]
    payload["comments"] = [{"id": row.id, "author_name": row.author_name, "content": row.content,
                             "created_at": row.created_at.isoformat(timespec="seconds") if row.created_at else ""}
                            for row in db.query(AnalysisComment).filter(AnalysisComment.token == share.token).order_by(AnalysisComment.created_at.asc()).all()]
    return payload


@app.get("/shared/{token}")
def get_shared(token: str):
    with get_session() as db:
        share = db.get(AnalysisShare, token)
        if share is None or (share.expires_at and share.expires_at < datetime.now()):
            raise HTTPException(status_code=404, detail="分享链接不存在或已过期")
        return _public_share_payload(db, share)


@app.post("/shared/{token}/comments")
def add_shared_comment(token: str, payload: dict):
    content = str(payload.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="评论不能为空")
    with get_session() as db:
        share = db.get(AnalysisShare, token)
        if share is None or not share.allow_comments:
            raise HTTPException(status_code=404, detail="分享链接不存在或不允许评论")
        row = AnalysisComment(token=token, author_name=str(payload.get("author_name") or "访客")[:64], content=content[:2000])
        db.add(row)
        db.commit()
        return {"id": row.id, "author_name": row.author_name, "content": row.content}


def _next_schedule(text: str, now: datetime) -> datetime:
    raw = str(text or "").strip().lower()
    match = re.fullmatch(r"every\s+(\d+)\s*([mhd])", raw)
    if match:
        amount, unit = int(match.group(1)), match.group(2)
        seconds = amount * {"m": 60, "h": 3600, "d": 86400}[unit]
        return now + __import__("datetime").timedelta(seconds=seconds)
    match = re.fullmatch(r"daily\s+(\d{1,2}):(\d{2})", raw)
    if match:
        target = now.replace(hour=int(match.group(1)), minute=int(match.group(2)), second=0, microsecond=0)
        return target if target > now else target + __import__("datetime").timedelta(days=1)
    raise ValueError("调度格式支持 every 15m / every 2h / daily 09:00")


def _execute_schedule(job_id: str, user_id: int) -> dict:
    with get_session() as db:
        job = db.query(ScheduleJob).filter(ScheduleJob.id == job_id, ScheduleJob.user_id == user_id).first()
        if job is None:
            raise ValueError("调度任务不存在")
        output = {"job": job.name, "type": job.job_type, "simulated_email": True,
                  "recipients": _json_value(job.recipients_json, []), "executed_at": datetime.now().isoformat(timespec="seconds")}
        error = None
        try:
            if job.job_type == "upload":
                source = os.path.abspath(job.source_path or "")
                ext = os.path.splitext(source)[1].lower()
                if not os.path.isfile(source) or ext not in kb.KB_EXTENSIONS:
                    raise ValueError("定时上传源文件不存在或类型不支持")
                target = os.path.join(KB_DIR, f"{uuid.uuid4().hex}{ext}")
                with open(source, "rb") as src, open(target, "wb") as dst:
                    dst.write(src.read())
                doc = kb.register_document(target, ext, os.path.basename(source), user_id=user_id)
                output["uploaded"] = doc
            else:
                docs = []
                for doc_id in _json_value(job.dataset_ids_json, []):
                    doc = kb.get_document(doc_id, user_id=user_id)
                    docs.append({"doc_id": doc_id, "filename": doc.get("filename"), "path": doc.get("path")})
                output["question"] = job.question or "定时分析报告"
                output["datasets"] = []
                for item in docs:
                    try:
                        summary = build_summary(load_dataframe(item["path"], os.path.splitext(item["filename"])[1].lower()))
                        output["datasets"].append({"doc_id": item["doc_id"], "filename": item["filename"], "rows": summary["rows"], "cols": summary["cols"]})
                    except Exception as exc:
                        output["datasets"].append({"doc_id": item["doc_id"], "filename": item["filename"], "error": str(exc)})
                output["message"] = "已生成模拟分析报告；当前调度器不调用真实邮件服务。"
        except Exception as exc:
            error = str(exc)
        run = ScheduleRun(job_id=job_id, status="failed" if error else "success", simulated_email=True,
                          output_json=json.dumps(output, ensure_ascii=False), error_msg=error)
        job.last_run_at = datetime.now()
        try:
            job.next_run_at = _next_schedule(job.schedule_text, job.last_run_at)
        except ValueError:
            job.next_run_at = None
        db.add(run)
        db.commit()
        return {"id": run.id, "status": run.status, "output": output, "error": error}


@app.get("/schedules")
def list_schedules(user: User = Depends(get_current_user)):
    with get_session() as db:
        rows = db.query(ScheduleJob).filter(ScheduleJob.user_id == user.id).order_by(ScheduleJob.created_at.desc()).all()
        return [{"id": row.id, "name": row.name, "job_type": row.job_type, "schedule_text": row.schedule_text,
                 "dataset_ids": _json_value(row.dataset_ids_json, []), "question": row.question,
                 "recipients": _json_value(row.recipients_json, []), "enabled": row.enabled,
                 "last_run_at": row.last_run_at.isoformat(timespec="seconds") if row.last_run_at else None,
                 "next_run_at": row.next_run_at.isoformat(timespec="seconds") if row.next_run_at else None} for row in rows]


@app.post("/schedules")
def create_schedule(payload: dict, user: User = Depends(get_current_user)):
    try:
        next_run = _next_schedule(str(payload.get("schedule_text") or ""), datetime.now())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    job = ScheduleJob(id=uuid.uuid4().hex, user_id=user.id, name=str(payload.get("name") or "定时任务")[:128],
                      job_type=str(payload.get("job_type") or "analysis"), schedule_text=str(payload.get("schedule_text")),
                      dataset_ids_json=json.dumps(payload.get("dataset_ids") or [], ensure_ascii=False),
                      question=str(payload.get("question") or "")[:2000], source_path=str(payload.get("source_path") or "")[:512],
                      recipients_json=json.dumps(payload.get("recipients") or [], ensure_ascii=False), next_run_at=next_run)
    if job.job_type not in ("analysis", "upload"):
        raise HTTPException(status_code=400, detail="任务类型只支持 analysis 或 upload")
    result = {"id": job.id, "name": job.name, "next_run_at": next_run.isoformat(timespec="seconds")}
    with get_session() as db:
        db.add(job)
        db.commit()
    return result


@app.post("/schedules/{job_id}/run")
def run_schedule(job_id: str, user: User = Depends(get_current_user)):
    try:
        return _execute_schedule(job_id, user.id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))


@app.patch("/schedules/{job_id}")
def update_schedule(job_id: str, payload: dict, user: User = Depends(get_current_user)):
    with get_session() as db:
        job = db.query(ScheduleJob).filter(ScheduleJob.id == job_id, ScheduleJob.user_id == user.id).first()
        if job is None:
            raise HTTPException(status_code=404, detail="调度任务不存在")
        if "enabled" in payload:
            job.enabled = bool(payload["enabled"])
        if "name" in payload:
            job.name = str(payload["name"])[:128]
        db.commit()
    return {"ok": True}


@app.delete("/schedules/{job_id}")
def delete_schedule(job_id: str, user: User = Depends(get_current_user)):
    with get_session() as db:
        job = db.query(ScheduleJob).filter(ScheduleJob.id == job_id, ScheduleJob.user_id == user.id).first()
        if job is None:
            raise HTTPException(status_code=404, detail="调度任务不存在")
        db.delete(job)
        db.commit()
    return {"ok": True}


def _schedule_worker():
    while True:
        try:
            now = datetime.now()
            with get_session() as db:
                due = db.query(ScheduleJob).filter(ScheduleJob.enabled.is_(True),
                    ScheduleJob.next_run_at.isnot(None), ScheduleJob.next_run_at <= now).all()
                jobs = [(row.id, row.user_id) for row in due]
            for job_id, user_id in jobs:
                try:
                    _execute_schedule(job_id, user_id)
                except Exception as exc:
                    print("schedule execution failed:", exc, file=sys.stderr)
        except Exception as exc:
            print("schedule worker failed:", exc, file=sys.stderr)
        time.sleep(30)



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
        s = get_owned_session(db, session_id, user)
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
        s = get_owned_session(db, session_id, user)
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
    """返回会话消息历史，并恢复已保存的分析表格/图表结果。"""
    with get_session() as db:
        s = get_owned_session(db, session_id, user)
        msgs = (db.query(ChatMessage)
                .filter(ChatMessage.session_id == session_id)
                .order_by(ChatMessage.id.asc())
                .all())
        results = (db.query(AnalysisResult)
                   .filter(AnalysisResult.session_id == session_id,
                           AnalysisResult.user_id == user.id)
                   .order_by(AnalysisResult.id.asc())
                   .all())
        result_queue = list(results)

        def result_payload(item: AnalysisResult) -> dict:
            def parse_json(raw: Optional[str]):
                if not raw:
                    return None
                try:
                    return json.loads(raw)
                except (TypeError, json.JSONDecodeError):
                    return None

            payload = {
                "stdout": item.stdout,
                "table": parse_json(item.table_json),
                "chart": parse_json(item.chart_json),
                "datasets": parse_json(item.dataset_json),
                "conclusion": item.conclusion,
                "execution_ms": item.execution_ms,
                "created_at": item.created_at.isoformat(timespec="seconds") if item.created_at else None,
            }
            if item.error_msg:
                payload["error"] = item.error_msg
            return payload

        messages = []
        for message in msgs:
            item = {"role": message.role, "content": message.content}
            if message.role == "assistant" and result_queue:
                item["result"] = result_payload(result_queue.pop(0))
            messages.append(item)
        return {
            "session_id": session_id,
            "title": s.title or "新会话",
            "mode": s.mode,
            "messages": messages,
        }


@app.get("/")
def index():
    # 根路由不鉴权：前端在加载时调 /auth/me 判断登录态
    # 加 no-cache 头：避免浏览器缓存旧版前端，每次启动都拉最新 HTML
    frontend_index = os.path.join(FRONTEND_DIST_DIR, "index.html")
    if not os.path.exists(frontend_index):
        raise HTTPException(status_code=503, detail="前端尚未构建，请先运行 npm --prefix frontend run build")
    return FileResponse(
        frontend_index,
        headers={"Cache-Control": "no-cache, no-store, must-revalidate"},
    )


@app.get("/{frontend_path:path}")
def frontend_history_fallback(frontend_path: str):
    """生产模式下让 Vue Router 的 /workbench、/shared/* 刷新仍返回前端入口。"""
    if frontend_path.startswith(("api/", "assets/")) or frontend_path in {"favicon.svg"}:
        raise HTTPException(status_code=404, detail="Not Found")
    frontend_index = os.path.join(FRONTEND_DIST_DIR, "index.html")
    if not os.path.exists(frontend_index):
        raise HTTPException(status_code=404, detail="Frontend build not found")
    return FileResponse(frontend_index, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})


# 启动时确保默认 admin 用户存在（用 .env 的 ADMIN_PASSWORD 生成 bcrypt 哈希）
Base.metadata.create_all(bind=engine)
ensure_compat_schema()
ensure_default_user()
# 多用户隔离：向量库一致性维护（幂等）——补历史块 user_id + 清孤儿向量
try:
    _r = kb.backfill_vector_owner()
    if _r["fixed"] or _r["orphaned"]:
        print(f"[startup] vector maintenance: {_r['fixed']} chunks owned, "
              f"{_r['orphaned']} orphan doc-groups removed", file=sys.stderr)
except Exception as e:
    print(f"[startup] vector maintenance skipped: {e}", file=sys.stderr)


# 定时任务后台轮询：只执行本地任务并写入模拟邮件记录，不连接 SMTP。
_schedule_thread = threading.Thread(target=_schedule_worker, name="schedule-worker", daemon=True)
_schedule_thread.start()
