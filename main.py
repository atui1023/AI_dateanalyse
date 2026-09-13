import json
import hashlib
import html
import os
import re
import smtplib
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from datetime import datetime, timedelta
from email.message import EmailMessage
from types import SimpleNamespace
from typing import Dict, List, Optional
from urllib.request import Request as UrlRequest, urlopen

import pandas as pd
import psutil
from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from sqlalchemy import text as sql_text
from starlette.middleware.sessions import SessionMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

import auth
import anomaly_rules
import data_sources
import kb
import llm
from auth import get_current_user, require_admin
from password_utils import hash_password, verify_password
# 审计日志通过 auth.log_action 调用（用模块前缀更清晰，避免和本地变量混淆）
from db import (
    AlertRecord, AnalysisComment, AnalysisRelation, AnalysisResult, AnalysisShare, Base,
    ChatMessage, Dashboard, DashboardItem, DashboardWorkspaceShare, DatasetWorkspaceShare, FolderWorkspaceShare, ScheduleJob, ScheduleRun,
    MountedDataset, Session as DbSession, User, Workflow, WorkflowRun,
    WorkflowRunStep, WorkflowStep, Workspace, WorkspaceMember, UsageEvent,
    PlanSubscription, engine,
    ensure_compat_schema, ensure_default_user, get_session,
)

app = FastAPI(title="AI 数据分析")

# 轻量限流：保护登录、上传和模型接口，生产环境可替换为网关/Redis 限流。
try:
    _RATE_LIMIT_PER_MINUTE = max(10, min(int(os.getenv("RATE_LIMIT_PER_MINUTE", "120")), 10000))
except (TypeError, ValueError):
    _RATE_LIMIT_PER_MINUTE = 120
_rate_lock = threading.Lock()
_rate_buckets: dict[str, list[float]] = {}


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path in {"/health", "/docs", "/openapi.json"} or request.url.path.startswith("/assets/"):
            return await call_next(request)
        client = request.client.host if request.client else "unknown"
        now = time.monotonic()
        with _rate_lock:
            bucket = [stamp for stamp in _rate_buckets.get(client, []) if now - stamp < 60]
            if len(bucket) >= _RATE_LIMIT_PER_MINUTE:
                _rate_buckets[client] = bucket
                return JSONResponse({"detail": "请求过于频繁，请稍后再试"}, status_code=429)
            bucket.append(now)
            _rate_buckets[client] = bucket
        return await call_next(request)


app.add_middleware(RateLimitMiddleware)

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
# STORAGE_DIR 可指向独立磁盘、共享目录或对象存储同步目录，避免文件和数据库绑定在同一目录。
UPLOAD_DIR = os.path.abspath(os.getenv("STORAGE_DIR") or os.path.join(BASE_DIR, "uploads"))
KB_DIR = os.path.join(UPLOAD_DIR, "kb")
FRONTEND_DIST_DIR = os.path.join(BASE_DIR, "frontend", "dist")
os.makedirs(KB_DIR, exist_ok=True)


@app.get("/health")
def health_check():
    """供反向代理、部署平台和监控探针使用的轻量健康检查。"""
    checks = {"database": "ok", "storage": "ok", "knowledge_base": "ok"}
    try:
        with get_session() as db:
            db.execute(sql_text("SELECT 1"))
    except Exception as exc:
        checks["database"] = f"error: {type(exc).__name__}"
    if not os.path.isdir(UPLOAD_DIR) or not os.access(UPLOAD_DIR, os.W_OK):
        checks["storage"] = "error: not writable"
    overall = "ok" if all(value == "ok" for value in checks.values()) else "degraded"
    return {"status": overall, "checks": checks, "time": datetime.now().isoformat(timespec="seconds")}


@app.get("/data-sources/types")
def list_data_source_types(user: User = Depends(get_current_user)):
    return data_sources.list_source_types()


@app.post("/data-sources/test")
def test_data_source_connection(payload: dict, user: User = Depends(get_current_user)):
    try:
        result = data_sources.test_connection(payload)
        auth.log_action(user.id, "test_data_source", "data_source", str(payload.get("type") or "mysql"), detail=f"{result['host']}/{result['database']}")
        return result
    except (ValueError, RuntimeError) as exc:
        auth.log_action(user.id, "test_data_source_failed", "data_source", str(payload.get("type") or "mysql"), detail=str(exc)[:500])
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/data-sources/preview-table")
def preview_data_source_table(payload: dict, user: User = Depends(get_current_user)):
    try:
        result = data_sources.inspect_table(payload, str(payload.get("table") or ""), int(payload.get("preview_rows") or 20))
        return result
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/data-sources/import-table")
def import_data_source_table(payload: dict, user: User = Depends(get_current_user)):
    try:
        filename, content, metadata = data_sources.export_table(payload, str(payload.get("table") or ""), int(payload.get("max_rows") or 100000))
        save_path = os.path.join(KB_DIR, f"{uuid.uuid4().hex}.csv")
        with open(save_path, "wb") as handle:
            handle.write(content)
        df = load_dataframe(save_path, ".csv")
        doc = kb.register_document(save_path, ".csv", filename, user_id=user.id)
        summary = build_summary(df)
        mount_dataset(user.id, doc["doc_id"], {"path": save_path, "ext": ".csv", "filename": filename, "summary": summary, "doc_id": doc["doc_id"]})
        auth.log_action(user.id, "import_data_source_table", "dataset", doc["doc_id"], detail=f"{metadata['type']}:{metadata['table']}")
        return {"dataset_id": doc["doc_id"], "doc_id": doc["doc_id"], "filename": filename, "metadata": metadata, "summary": summary}
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/data-sources/import")
def import_remote_data_source(payload: dict, user: User = Depends(get_current_user)):
    """将 API/Webhook 的 CSV 或 JSON 快照导入为平台数据集。"""
    source_type = str(payload.get("type") or "api").lower()
    if source_type not in {"api", "webhook"}:
        raise HTTPException(status_code=400, detail="远程导入只支持 api 或 webhook")
    try:
        filename, content, _ = data_sources.fetch_remote_dataset(payload)
        ext = ".csv"
        save_path = os.path.join(KB_DIR, f"{uuid.uuid4().hex}{ext}")
        with open(save_path, "wb") as handle:
            handle.write(content)
        df = load_dataframe(save_path, ext)
        doc = kb.register_document(save_path, ext, filename, user_id=user.id)
        summary = build_summary(df)
        mount_dataset(user.id, doc["doc_id"], {"path": save_path, "ext": ext, "filename": filename, "summary": summary, "doc_id": doc["doc_id"]})
        auth.log_action(user.id, "import_remote_data_source", "dataset", doc["doc_id"], detail=f"{source_type}: {payload.get('url', '')[:200]}")
        return {"dataset_id": doc["doc_id"], "doc_id": doc["doc_id"], "filename": filename, "summary": summary}
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/analysis/anomalies")
def evaluate_anomalies(payload: dict, user: User = Depends(get_current_user)):
    table = payload.get("table") or {}
    if not isinstance(table, dict) or not isinstance(table.get("columns"), list) or not isinstance(table.get("rows"), list):
        raise HTTPException(status_code=400, detail="table 必须包含 columns 和 rows")
    alerts = anomaly_rules.evaluate(table, payload.get("rules") if isinstance(payload.get("rules"), dict) else {})
    persisted = []
    now = datetime.now()
    with get_session() as db:
        for alert in alerts:
            fingerprint = hashlib.sha256(json.dumps({
                "type": alert.get("type"),
                "column": alert.get("column"),
                "message": alert.get("message"),
            }, ensure_ascii=False, sort_keys=True).encode("utf-8")).hexdigest()
            record = db.query(AlertRecord).filter(
                AlertRecord.user_id == user.id,
                AlertRecord.fingerprint == fingerprint,
            ).first()
            if record is None:
                record = AlertRecord(
                    user_id=user.id,
                    fingerprint=fingerprint,
                    rule_key=str(alert.get("type") or "rule"),
                    message=str(alert.get("message") or "检测到异常"),
                )
                db.add(record)
                db.flush()
            else:
                record.occurrences += 1
                record.last_seen_at = now
                record.message = str(alert.get("message") or record.message)
            if record.muted_until and record.muted_until > now:
                continue
            persisted.append({**alert, "id": record.id, "status": record.status, "occurrences": record.occurrences})
        db.commit()
    auth.log_action(user.id, "evaluate_anomalies", "analysis", "rules", detail=f"alerts={len(alerts)}")
    return {"alerts": persisted, "count": len(persisted), "detected_count": len(alerts)}


@app.get("/datasets/{doc_id}/quality")
def dataset_quality(doc_id: str, user: User = Depends(get_current_user)):
    try:
        record = load_dataset_records(user.id, [doc_id])[0]
        frame = load_dataframe(record["path"], record["ext"])
    except (ValueError, KeyError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=f"无法读取数据集：{exc}") from exc
    columns = []
    for name in frame.columns:
        series = frame[name]
        item = {"name": str(name), "type": str(series.dtype), "missing": int(series.isna().sum()),
                "unique": int(series.nunique(dropna=True)), "missing_rate": round(float(series.isna().mean() * 100), 2)}
        if pd.api.types.is_numeric_dtype(series):
            numeric = pd.to_numeric(series, errors="coerce").dropna()
            if not numeric.empty:
                item.update({"min": float(numeric.min()), "max": float(numeric.max()), "mean": round(float(numeric.mean()), 4)})
        columns.append(item)
    duplicate_rows = int(frame.duplicated().sum())
    missing_cells = int(frame.isna().sum().sum())
    numeric_frame = frame.select_dtypes(include="number")
    outlier_count = 0
    if not numeric_frame.empty:
        for name in numeric_frame.columns:
            series = numeric_frame[name].dropna()
            if len(series) >= 4:
                q1, q3 = series.quantile(.25), series.quantile(.75)
                iqr = q3 - q1
                if iqr > 0:
                    outlier_count += int(((series < q1 - 1.5 * iqr) | (series > q3 + 1.5 * iqr)).sum())
    issues = []
    if missing_cells:
        issues.append({"level": "warning", "message": f"发现 {missing_cells} 个空值单元格"})
    if duplicate_rows:
        issues.append({"level": "warning", "message": f"发现 {duplicate_rows} 行重复记录"})
    if outlier_count:
        issues.append({"level": "info", "message": f"数值字段检测到约 {outlier_count} 个箱线图异常值"})
    if not issues:
        issues.append({"level": "success", "message": "未发现明显的数据质量问题"})
    return {"dataset_id": doc_id, "filename": record["filename"], "rows": len(frame), "columns": len(frame.columns),
            "missing_cells": missing_cells, "duplicate_rows": duplicate_rows, "outlier_count": outlier_count,
            "quality_score": max(0, round(100 - min(60, missing_cells / max(1, len(frame) * max(1, len(frame.columns))) * 100) - min(25, duplicate_rows / max(1, len(frame)) * 100) - min(15, outlier_count / max(1, len(frame)) * 100), 1)),
            "columns_detail": columns, "issues": issues}


def _alert_out(row: AlertRecord) -> dict:
    return {
        "id": row.id, "rule_key": row.rule_key, "message": row.message,
        "status": row.status, "occurrences": row.occurrences,
        "muted_until": row.muted_until.isoformat(timespec="seconds") if row.muted_until else None,
        "last_seen_at": row.last_seen_at.isoformat(timespec="seconds") if row.last_seen_at else None,
    }


@app.get("/alerts")
def list_alerts(limit: int = 100, user: User = Depends(get_current_user)):
    limit = max(1, min(limit, 500))
    with get_session() as db:
        rows = db.query(AlertRecord).filter(AlertRecord.user_id == user.id).order_by(AlertRecord.last_seen_at.desc()).limit(limit).all()
        return [_alert_out(row) for row in rows]


@app.post("/alerts/{alert_id}/ack")
def acknowledge_alert(alert_id: int, user: User = Depends(get_current_user)):
    with get_session() as db:
        row = db.query(AlertRecord).filter(AlertRecord.id == alert_id, AlertRecord.user_id == user.id).first()
        if row is None:
            raise HTTPException(status_code=404, detail="告警不存在")
        row.status = "acknowledged"
        db.commit()
        return _alert_out(row)


@app.post("/alerts/{alert_id}/mute")
def mute_alert(alert_id: int, payload: dict, user: User = Depends(get_current_user)):
    try:
        days = max(1, min(int(payload.get("days") or 1), 365))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="days 必须是整数")
    with get_session() as db:
        row = db.query(AlertRecord).filter(AlertRecord.id == alert_id, AlertRecord.user_id == user.id).first()
        if row is None:
            raise HTTPException(status_code=404, detail="告警不存在")
        row.muted_until = datetime.now() + timedelta(days=days)
        row.status = "muted"
        db.commit()
        return _alert_out(row)


def _workspace_member(db, workspace_id: str, user_id: int):
    return db.query(WorkspaceMember).filter(
        WorkspaceMember.workspace_id == workspace_id,
        WorkspaceMember.user_id == user_id,
    ).first()


def _dashboard_accessible(db, dashboard_id: str, user_id: int, write: bool = False):
    board = db.query(Dashboard).filter(Dashboard.id == dashboard_id).first()
    if board is None:
        return None
    if board.user_id == user_id:
        return board
    if write:
        return None
    shared = (db.query(DashboardWorkspaceShare)
              .join(WorkspaceMember, WorkspaceMember.workspace_id == DashboardWorkspaceShare.workspace_id)
              .filter(DashboardWorkspaceShare.dashboard_id == dashboard_id,
                      WorkspaceMember.user_id == user_id).first())
    return board if shared else None


def _document_accessible(db, doc_id: str, user_id: int, write: bool = False):
    document = db.query(kb.KbDocument).filter(kb.KbDocument.doc_id == doc_id).first()
    if document is None:
        return None
    if document.user_id == user_id:
        return document
    if write:
        return None
    shared = (db.query(DatasetWorkspaceShare)
              .join(WorkspaceMember, WorkspaceMember.workspace_id == DatasetWorkspaceShare.workspace_id)
              .filter(DatasetWorkspaceShare.doc_id == doc_id, WorkspaceMember.user_id == user_id).first())
    folder_shared = (db.query(FolderWorkspaceShare)
                     .join(WorkspaceMember, WorkspaceMember.workspace_id == FolderWorkspaceShare.workspace_id)
                     .filter(FolderWorkspaceShare.folder_id == document.folder_id,
                             WorkspaceMember.user_id == user_id).first())
    return document if shared or folder_shared else None


def _folder_accessible(db, folder_id: str, user_id: int, write: bool = False):
    folder = db.query(kb.KbFolder).filter(kb.KbFolder.id == folder_id).first()
    if folder is None:
        return None
    if folder.user_id == user_id:
        return folder
    if write:
        return None
    shared = (db.query(FolderWorkspaceShare)
              .join(WorkspaceMember, WorkspaceMember.workspace_id == FolderWorkspaceShare.workspace_id)
              .filter(FolderWorkspaceShare.folder_id == folder_id, WorkspaceMember.user_id == user_id).first())
    return folder if shared else None


def _workspace_out(db, workspace: Workspace, user_id: int) -> dict:
    member = _workspace_member(db, workspace.id, user_id)
    return {"id": workspace.id, "name": workspace.name, "owner_id": workspace.owner_id,
            "role": member.role if member else None,
            "created_at": workspace.created_at.isoformat(timespec="seconds")}


@app.get("/workspaces")
def list_workspaces(user: User = Depends(get_current_user)):
    with get_session() as db:
        rows = (db.query(Workspace).join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
                .filter(WorkspaceMember.user_id == user.id).order_by(Workspace.created_at.asc()).all())
        return [_workspace_out(db, row, user.id) for row in rows]


@app.post("/workspaces")
def create_workspace(payload: dict, user: User = Depends(get_current_user)):
    name = str(payload.get("name") or "我的团队").strip()[:128]
    if not name:
        raise HTTPException(status_code=400, detail="团队名称不能为空")
    with get_session() as db:
        workspace = Workspace(id=uuid.uuid4().hex[:32], owner_id=user.id, name=name)
        db.add(workspace)
        db.flush()
        db.add(WorkspaceMember(workspace_id=workspace.id, user_id=user.id, role="owner"))
        db.commit()
        result = _workspace_out(db, workspace, user.id)
    auth.log_action(user.id, "create_workspace", "workspace", result["id"])
    return result


@app.get("/workspaces/{workspace_id}/members")
def list_workspace_members(workspace_id: str, user: User = Depends(get_current_user)):
    with get_session() as db:
        if _workspace_member(db, workspace_id, user.id) is None:
            raise HTTPException(status_code=404, detail="团队不存在或无权访问")
        rows = db.query(WorkspaceMember, User).join(User, User.id == WorkspaceMember.user_id).filter(WorkspaceMember.workspace_id == workspace_id).all()
        return [{"id": m.id, "user_id": u.id, "username": u.username, "display_name": u.display_name, "role": m.role,
                 "created_at": m.created_at.isoformat(timespec="seconds")} for m, u in rows]


@app.post("/workspaces/{workspace_id}/members")
def invite_workspace_member(workspace_id: str, payload: dict, user: User = Depends(get_current_user)):
    username = str(payload.get("username") or "").strip()
    role = str(payload.get("role") or "viewer").lower()
    if role not in {"editor", "viewer"} or not username:
        raise HTTPException(status_code=400, detail="需要有效用户名和 editor/viewer 角色")
    with get_session() as db:
        inviter = _workspace_member(db, workspace_id, user.id)
        if inviter is None or inviter.role not in {"owner", "editor"}:
            raise HTTPException(status_code=403, detail="只有团队所有者或编辑者可以邀请成员")
        target = db.query(User).filter(User.username == username).first()
        if target is None:
            raise HTTPException(status_code=404, detail="用户不存在，请先注册账号")
        if _workspace_member(db, workspace_id, target.id):
            raise HTTPException(status_code=409, detail="用户已在团队中")
        member = WorkspaceMember(workspace_id=workspace_id, user_id=target.id, role=role)
        db.add(member)
        db.commit()
        return {"id": member.id, "user_id": target.id, "username": target.username, "role": member.role}


@app.delete("/workspaces/{workspace_id}/members/{member_id}")
def remove_workspace_member(workspace_id: str, member_id: int, user: User = Depends(get_current_user)):
    with get_session() as db:
        inviter = _workspace_member(db, workspace_id, user.id)
        member = db.query(WorkspaceMember).filter(WorkspaceMember.id == member_id, WorkspaceMember.workspace_id == workspace_id).first()
        if inviter is None or inviter.role != "owner" or member is None:
            raise HTTPException(status_code=403 if inviter else 404, detail="无权操作团队成员")
        if member.role == "owner":
            raise HTTPException(status_code=400, detail="不能移除团队所有者")
        db.delete(member)
        db.commit()
        return {"ok": True}


PLANS = {
    "free": {"name": "免费版", "monthly_limit": 100, "storage_mb": 500},
    "pro": {"name": "专业版", "monthly_limit": 2000, "storage_mb": 10240},
    "business": {"name": "企业版", "monthly_limit": 20000, "storage_mb": 102400},
}


def _ensure_subscription(db, user_id: int) -> PlanSubscription:
    row = db.query(PlanSubscription).filter(PlanSubscription.user_id == user_id).first()
    if row is None:
        row = PlanSubscription(user_id=user_id, plan="free", monthly_limit=PLANS["free"]["monthly_limit"])
        db.add(row)
        db.flush()
    return row


@app.get("/billing/plans")
def list_billing_plans(user: User = Depends(get_current_user)):
    return [{"id": key, **value} for key, value in PLANS.items()]


@app.get("/billing/usage")
def billing_usage(user: User = Depends(get_current_user)):
    with get_session() as db:
        subscription = _ensure_subscription(db, user.id)
        events = db.query(UsageEvent).filter(UsageEvent.user_id == user.id, UsageEvent.created_at >= subscription.period_start).all()
        db.commit()
        return {"plan": subscription.plan, "status": subscription.status, "used_units": subscription.used_units,
                "monthly_limit": subscription.monthly_limit, "storage_mb": PLANS.get(subscription.plan, PLANS["free"])["storage_mb"],
                "events": {"count": len(events), "execution_ms": sum(e.execution_ms for e in events),
                           "tokens": sum(e.tokens for e in events), "cost_usd": round(sum(e.cost_usd for e in events), 6)}}


@app.post("/billing/subscribe")
def subscribe_plan(payload: dict, user: User = Depends(get_current_user)):
    plan = str(payload.get("plan") or "free").lower()
    if plan not in PLANS:
        raise HTTPException(status_code=400, detail="不支持的套餐")
    with get_session() as db:
        subscription = _ensure_subscription(db, user.id)
        subscription.plan = plan
        subscription.monthly_limit = PLANS[plan]["monthly_limit"]
        subscription.status = "active"
        db.commit()
        return {"plan": plan, "status": subscription.status, "monthly_limit": subscription.monthly_limit,
                "payment_required": plan != "free", "message": "当前为本地套餐状态接口，支付 provider 尚未接入"}


@app.get("/admin/metrics")
def admin_metrics(admin: User = Depends(require_admin)):
    with get_session() as db:
        total_users = db.query(User).count()
        active_users = db.query(UsageEvent.user_id).distinct().count()
        total_results = db.query(AnalysisResult).count()
        success_runs = db.query(ScheduleRun).filter(ScheduleRun.status == "success").count()
        failed_runs = db.query(ScheduleRun).filter(ScheduleRun.status == "failed").count()
        events = db.query(UsageEvent).all()
        return {"users": {"total": total_users, "active": active_users}, "analysis_results": total_results,
                "tasks": {"success": success_runs, "failed": failed_runs,
                          "success_rate": round(success_runs / (success_runs + failed_runs), 4) if success_runs + failed_runs else 0},
                "usage": {"events": len(events), "tokens": sum(e.tokens for e in events),
                          "execution_ms": sum(e.execution_ms for e in events), "cost_usd": round(sum(e.cost_usd for e in events), 6)}}

# 生产/桌面启动统一托管 Vue 构建产物。
if os.path.isdir(os.path.join(FRONTEND_DIST_DIR, "assets")):
    app.mount("/assets", StaticFiles(directory=os.path.join(FRONTEND_DIST_DIR, "assets")), name="frontend-assets")
    app.mount("/favicon.svg", StaticFiles(directory=FRONTEND_DIST_DIR), name="frontend-favicon")

def mount_dataset(user_id: int, doc_id: str, info: dict) -> None:
    """Persist a mounted dataset so it survives API restarts."""
    with get_session() as db:
        row = db.query(MountedDataset).filter(
            MountedDataset.user_id == user_id, MountedDataset.doc_id == doc_id
        ).first()
        summary_json = json.dumps(info.get("summary") or {}, ensure_ascii=False)
        if row is None:
            db.add(MountedDataset(user_id=user_id, doc_id=doc_id, summary_json=summary_json))
        else:
            row.summary_json = summary_json
            row.updated_at = datetime.now()
        db.commit()


def unmount_dataset(user_id: int, doc_id: str) -> None:
    with get_session() as db:
        db.query(MountedDataset).filter(
            MountedDataset.user_id == user_id, MountedDataset.doc_id == doc_id
        ).delete()
        db.commit()


def get_active_datasets(user_id: int, doc_ids: List[str]) -> List[dict]:
    """Load mounted datasets from MySQL in the requested df1/df2 order."""
    if not doc_ids:
        return []
    with get_session() as db:
        rows = db.query(MountedDataset).filter(
            MountedDataset.user_id == user_id,
            MountedDataset.doc_id.in_(doc_ids),
        ).all()
        summaries = {row.doc_id: _json_value(row.summary_json, {}) for row in rows}
    active = []
    for doc_id in doc_ids:
        if doc_id not in summaries:
            continue
        try:
            doc = kb.get_document(doc_id, user_id=user_id)
        except ValueError:
            continue
        active.append({
            "doc_id": doc_id,
            "path": doc["path"],
            "ext": doc["ext"],
            "filename": doc["filename"],
            "summary": summaries[doc_id],
        })
    return active


def mounted_dataset_ids(user_id: int) -> set[str]:
    with get_session() as db:
        return {row.doc_id for row in db.query(MountedDataset).filter(MountedDataset.user_id == user_id).all()}


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
UPLOAD_CHUNK_SIZE = 1024 * 1024


def _env_int(name: str, default: int, minimum: int, maximum: int) -> int:
    """读取带边界的整数配置，避免 .env 填错导致请求直接 500。"""
    try:
        value = int(os.getenv(name, str(default)))
    except (TypeError, ValueError):
        value = default
    return max(minimum, min(value, maximum))


async def save_upload_limited(file: UploadFile, target_path: str, max_size: int, reject_empty: bool = False) -> int:
    total = 0
    has_content = False
    try:
        with open(target_path, "wb") as target:
            while chunk := await file.read(UPLOAD_CHUNK_SIZE):
                total += len(chunk)
                if total > max_size:
                    raise HTTPException(status_code=400, detail=f"文件大小不能超过 {max_size // (1024 * 1024)}MB")
                has_content = has_content or bool(chunk.strip())
                target.write(chunk)
    except Exception:
        if os.path.exists(target_path):
            os.remove(target_path)
        raise
    if reject_empty and not has_content:
        os.remove(target_path)
        raise HTTPException(status_code=400, detail="文件内容为空，请先在文档中写入文字再上传")
    return total


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
    """读取 CSV / Excel 为 DataFrame，兼容常见中文和 Windows 导出编码。"""
    if ext == ".csv":
        last_error = None
        for encoding in ("utf-8-sig", "utf-8", "gbk", "utf-16"):
            try:
                return pd.read_csv(path, encoding=encoding)
            except (UnicodeDecodeError, pd.errors.ParserError) as exc:
                last_error = exc
        raise ValueError(f"CSV 文件解析失败：{last_error}")
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
    outlier_columns = []
    for col in df.select_dtypes(include="number").columns:
        values = df[col].dropna()
        if len(values) < 4:
            continue
        q1, q3 = values.quantile([0.25, 0.75])
        iqr = q3 - q1
        if iqr > 0:
            count = int(((values < q1 - 1.5 * iqr) | (values > q3 + 1.5 * iqr)).sum())
            if count:
                outlier_columns.append({"column": str(col), "count": count})
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
    if outlier_columns:
        quality_issues.append({
            "level": "warning",
            "code": "outliers",
            "message": f"{len(outlier_columns)} 个数值字段存在异常值，共 {sum(x['count'] for x in outlier_columns)} 个",
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
            "outlier_columns": outlier_columns,
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

    # 统一存入知识库目录，由 kb 模块登记并触发后台向量化
    save_path = os.path.join(KB_DIR, f"{uuid.uuid4().hex}{ext}")
    await save_upload_limited(file, save_path, MAX_FILE_SIZE)

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


@app.get("/datasets")
def list_mounted_datasets(user: User = Depends(get_current_user)):
    with get_session() as db:
        ids = [row.doc_id for row in db.query(MountedDataset).filter(
            MountedDataset.user_id == user.id
        ).order_by(MountedDataset.created_at.asc()).all()]
    return get_active_datasets(user.id, ids)


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
    mounted_ids = mounted_dataset_ids(user.id)
    if doc_id not in mounted_ids and len(mounted_ids) >= MAX_DATASETS:
        raise HTTPException(status_code=400, detail=f"最多同时挂载 {MAX_DATASETS} 个数据集，请先卸载部分文件")
    mount_dataset(user.id, doc_id, {
        "path": rec["path"],
        "ext": rec["ext"],
        "filename": rec["filename"],
        "summary": summary,
        "doc_id": doc_id,
    })
    return {"dataset_id": doc_id, "filename": rec["filename"], "summary": summary}


@app.put("/kb/documents/{doc_id}/workspace-share")
def share_dataset_to_workspace(doc_id: str, payload: dict, user: User = Depends(get_current_user)):
    workspace_id = str(payload.get("workspace_id") or "").strip()
    if not workspace_id:
        raise HTTPException(status_code=400, detail="请选择团队")
    with get_session() as db:
        document = db.query(kb.KbDocument).filter(kb.KbDocument.doc_id == doc_id, kb.KbDocument.user_id == user.id).first()
        member = _workspace_member(db, workspace_id, user.id)
        if document is None:
            raise HTTPException(status_code=404, detail="文档不存在")
        if member is None or member.role != "owner":
            raise HTTPException(status_code=403, detail="只有团队所有者可以共享数据集")
        share = db.query(DatasetWorkspaceShare).filter(DatasetWorkspaceShare.doc_id == doc_id).first()
        if share is None:
            share = DatasetWorkspaceShare(doc_id=doc_id, workspace_id=workspace_id, created_by=user.id)
            db.add(share)
        else:
            share.workspace_id = workspace_id
        db.commit()
        return {"doc_id": doc_id, "workspace_id": workspace_id, "shared": True}


@app.delete("/kb/documents/{doc_id}/workspace-share")
def unshare_dataset_from_workspace(doc_id: str, user: User = Depends(get_current_user)):
    with get_session() as db:
        document = db.query(kb.KbDocument).filter(kb.KbDocument.doc_id == doc_id, kb.KbDocument.user_id == user.id).first()
        if document is None:
            raise HTTPException(status_code=404, detail="文档不存在")
        share = db.query(DatasetWorkspaceShare).filter(DatasetWorkspaceShare.doc_id == doc_id).first()
        if share:
            db.delete(share)
            db.commit()
        return {"doc_id": doc_id, "shared": False}


@app.post("/kb/documents/{doc_id}/clean")
def kb_clean_document(doc_id: str, request: Request, user: User = Depends(get_current_user)):
    """生成清洗副本：移除全空行和重复行，原始文件保持不变。"""
    with get_session() as db:
        document = _document_accessible(db, doc_id, user.id)
        rec = kb._doc_to_dict(document) if document is not None else None
    if rec is None:
        raise HTTPException(status_code=404, detail="文档不存在或无权访问")
    if rec["ext"] not in ALLOWED_EXT:
        raise HTTPException(status_code=400, detail="仅 CSV / Excel 表格文件支持清洗")
    if not os.path.exists(rec["path"]):
        raise HTTPException(status_code=400, detail="原始文件已丢失，请重新上传")
    try:
        frame = load_dataframe(rec["path"], rec["ext"])
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"文件解析失败：{e}")
    original_rows = len(frame.index)
    cleaned = frame.dropna(how="all").drop_duplicates().reset_index(drop=True)
    removed_rows = original_rows - len(cleaned.index)
    if rec["ext"] == ".csv":
        clean_ext = ".csv"
        clean_path = os.path.join(KB_DIR, f"{uuid.uuid4().hex}{clean_ext}")
        cleaned.to_csv(clean_path, index=False, encoding="utf-8-sig")
    else:
        clean_ext = ".xlsx"
        clean_path = os.path.join(KB_DIR, f"{uuid.uuid4().hex}{clean_ext}")
        cleaned.to_excel(clean_path, index=False)
    clean_doc = kb.register_document(
        clean_path,
        clean_ext,
        f"{os.path.splitext(rec['filename'])[0]}_清洗副本{clean_ext}",
        folder_id=rec.get("folder_id"),
        user_id=user.id,
    )
    auth.log_action(user.id, "clean_document", "document", clean_doc["doc_id"],
                    detail=f"source={doc_id}; removed_rows={removed_rows}",
                    ip=auth._client_ip(request))
    return {
        "doc_id": clean_doc["doc_id"],
        "filename": clean_doc["filename"],
        "status": clean_doc["status"],
        "removed_rows": removed_rows,
        "summary": build_summary(cleaned),
    }


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

    save_path = os.path.join(KB_DIR, f"{uuid.uuid4().hex}{ext}")
    await save_upload_limited(file, save_path, kb.MAX_KB_FILE_SIZE, ext in (".txt", ".md", ".csv"))

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
    own = kb.list_documents(user_id=user.id)
    own_ids = {item["doc_id"] for item in own}
    with get_session() as db:
        shared_rows = (db.query(kb.KbDocument, DatasetWorkspaceShare.workspace_id)
                       .join(WorkspaceMember, WorkspaceMember.user_id == user.id)
                       .outerjoin(DatasetWorkspaceShare, DatasetWorkspaceShare.doc_id == kb.KbDocument.doc_id)
                       .outerjoin(FolderWorkspaceShare, FolderWorkspaceShare.folder_id == kb.KbDocument.folder_id)
                       .filter(kb.KbDocument.user_id != user.id,
                               ((DatasetWorkspaceShare.workspace_id == WorkspaceMember.workspace_id) |
                                (FolderWorkspaceShare.workspace_id == WorkspaceMember.workspace_id))).all())
        shared = []
        for document, workspace_id in shared_rows:
            if document.doc_id in own_ids:
                continue
            item = kb._doc_to_dict(document)
            item["read_only"] = True
            item["shared_workspace_id"] = workspace_id or next((row.workspace_id for row in db.query(FolderWorkspaceShare).filter(FolderWorkspaceShare.folder_id == document.folder_id).all()), None)
            shared.append(item)
    return own + shared


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
    save_path = os.path.join(KB_DIR, f"{uuid.uuid4().hex}{ext}")
    file_size = await save_upload_limited(file, save_path, kb.MAX_KB_FILE_SIZE, ext in (".txt", ".md", ".csv"))
    try:
        doc = kb.replace_document(doc_id, save_path, ext, filename, file_size, user_id=user.id)
    except ValueError as e:
        if os.path.exists(save_path):
            os.remove(save_path)
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        if os.path.exists(save_path):
            os.remove(save_path)
        raise HTTPException(status_code=400, detail=f"上传新版本失败：{e}")
    is_mounted = doc_id in mounted_dataset_ids(user.id)
    if is_mounted and ext in ALLOWED_EXT:
        try:
            summary = build_summary(load_dataframe(save_path, ext))
            mount_dataset(user.id, doc_id, {"summary": summary})
        except Exception:
            pass
    elif is_mounted:
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
    own = kb.list_folders(user_id=user.id)
    with get_session() as db:
        shared_rows = (db.query(kb.KbFolder, FolderWorkspaceShare.workspace_id)
                       .join(FolderWorkspaceShare, FolderWorkspaceShare.folder_id == kb.KbFolder.id)
                       .join(WorkspaceMember, WorkspaceMember.workspace_id == FolderWorkspaceShare.workspace_id)
                       .filter(WorkspaceMember.user_id == user.id, kb.KbFolder.user_id != user.id).all())
        shared = []
        for folder, workspace_id in shared_rows:
            item = kb._folder_to_dict(folder)
            item["read_only"] = True
            item["shared_workspace_id"] = workspace_id
            shared.append(item)
    return own + shared


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


@app.put("/kb/folders/{folder_id}/workspace-share")
def share_folder_to_workspace(folder_id: str, payload: dict, user: User = Depends(get_current_user)):
    workspace_id = str(payload.get("workspace_id") or "").strip()
    if not workspace_id:
        raise HTTPException(status_code=400, detail="请选择团队")
    with get_session() as db:
        folder = db.query(kb.KbFolder).filter(kb.KbFolder.id == folder_id, kb.KbFolder.user_id == user.id).first()
        member = _workspace_member(db, workspace_id, user.id)
        if folder is None or folder.is_system:
            raise HTTPException(status_code=404, detail="知识库不存在或不可共享")
        if member is None or member.role != "owner":
            raise HTTPException(status_code=403, detail="只有团队所有者可以共享知识库")
        share = db.query(FolderWorkspaceShare).filter(FolderWorkspaceShare.folder_id == folder_id).first()
        if share is None:
            share = FolderWorkspaceShare(folder_id=folder_id, workspace_id=workspace_id, created_by=user.id)
            db.add(share)
        else:
            share.workspace_id = workspace_id
        db.commit()
        return {"folder_id": folder_id, "workspace_id": workspace_id, "shared": True}


@app.delete("/kb/folders/{folder_id}/workspace-share")
def unshare_folder_from_workspace(folder_id: str, user: User = Depends(get_current_user)):
    with get_session() as db:
        folder = db.query(kb.KbFolder).filter(kb.KbFolder.id == folder_id, kb.KbFolder.user_id == user.id).first()
        if folder is None:
            raise HTTPException(status_code=404, detail="知识库不存在")
        share = db.query(FolderWorkspaceShare).filter(FolderWorkspaceShare.folder_id == folder_id).first()
        if share:
            db.delete(share)
            db.commit()
        return {"folder_id": folder_id, "shared": False}


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

    timeout_seconds = _env_int("ANALYSIS_TIMEOUT_SECONDS", 30, 1, 300)
    memory_limit = _env_int("ANALYSIS_MEMORY_LIMIT_MB", 1536, 1, 8192) * 1024 * 1024
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


def load_dataset_records(user_id: int, doc_ids: List[str]) -> List[dict]:
    records = []
    for doc_id in doc_ids:
        with get_session() as db:
            document = _document_accessible(db, doc_id, user_id)
            doc = kb._doc_to_dict(document) if document is not None else None
        if doc is None:
            raise ValueError("数据集不存在或无权访问")
        if doc.get("ext") not in ALLOWED_EXT:
            raise ValueError(f"{doc.get('filename')} 不是表格文件")
        if not os.path.exists(doc["path"]):
            raise FileNotFoundError(f"{doc.get('filename')} 原始文件已丢失")
        records.append({
            "doc_id": doc_id,
            "path": doc["path"],
            "ext": doc["ext"],
            "filename": doc["filename"],
            "summary": build_summary(load_dataframe(doc["path"], doc["ext"])),
        })
    return records


def execute_analysis_instruction(active_datasets: List[dict], instruction: str) -> dict:
    """Generate and execute one analysis instruction for chat, dashboards and schedules."""
    started_at = time.perf_counter()
    summary = "\n\n".join(dataset_summary_text(i + 1, item) for i, item in enumerate(active_datasets))
    response = llm.chat_model.invoke([
        {"role": "system", "content": llm.build_system_prompt(summary)},
        {"role": "user", "content": instruction},
    ])
    generated = response.content if hasattr(response, "content") else str(response)
    code = extract_code(generated)
    if not code:
        return {"stdout": generated, "table": None, "chart": None, "error": None,
                "code": None, "datasets": dataset_snapshot(active_datasets),
                "execution_ms": int((time.perf_counter() - started_at) * 1000)}
    result = run_analysis_with_retries(active_datasets, instruction, code)
    result["code"] = result.get("code") or code
    result["datasets"] = dataset_snapshot(active_datasets)
    result["execution_ms"] = int((time.perf_counter() - started_at) * 1000)
    return result


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


def delete_analysis_result(result_id: int, user_id: int) -> None:
    with get_session() as db:
        db.query(AnalysisResult).filter(
            AnalysisResult.id == result_id, AnalysisResult.user_id == user_id
        ).delete()
        db.commit()


def cleanup_generated_document(doc_id: Optional[str], path: Optional[str], user_id: int) -> None:
    if doc_id:
        try:
            kb.delete_document(doc_id, user_id=user_id)
        except Exception:
            pass
    if path and os.path.exists(path):
        try:
            os.remove(path)
        except OSError:
            pass

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
        "code": row.code,
        "title": row.title or row.question[:80],
        "is_favorite": row.is_favorite,
        "status": row.status,
        "stdout": row.stdout,
        "table": _json_value(row.table_json, None),
        "chart": _json_value(row.chart_json, None),
        "datasets": _json_value(row.dataset_json, []),
        "execution_ms": row.execution_ms,
        "error": row.error_msg,
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


_SENSITIVE_COLUMN_RE = re.compile(r"手机号|手机|电话|邮箱|email|phone|身份证|证件|银行卡|卡号", re.I)


def _mask_sensitive_table(table: Optional[dict]) -> Optional[dict]:
    """只在公开分享响应中脱敏常见个人联系方式和证件字段。"""
    if not table or not table.get("columns") or not table.get("rows"):
        return table
    columns = table["columns"]
    sensitive_indexes = [i for i, name in enumerate(columns) if _SENSITIVE_COLUMN_RE.search(str(name))]
    if not sensitive_indexes:
        return table
    masked = dict(table)
    masked["rows"] = [
        ["***" if index in sensitive_indexes and value not in (None, "") else value for index, value in enumerate(row)]
        for row in table["rows"]
    ]
    return masked


@app.get("/analysis/results")
def analysis_results(limit: int = 50, user: User = Depends(get_current_user)):
    with get_session() as db:
        rows = (db.query(AnalysisResult)
                .filter(AnalysisResult.user_id == user.id)
                .order_by(AnalysisResult.created_at.desc())
                .limit(max(1, min(limit, 200))).all())
        return [_result_view(row) for row in rows]


@app.delete("/analysis/results/{result_id}")
def delete_analysis_result_route(result_id: int, request: Request, user: User = Depends(get_current_user)):
    """删除本人保存的分析结果。仪表盘项目不会被级联删除，读取时会显示为空。"""
    with get_session() as db:
        row = _owned_result(db, result_id, user)
        db.delete(row)
        db.commit()
    auth.log_action(user.id, "delete_analysis_result", "analysis_result", str(result_id),
                    ip=auth._client_ip(request))
    return {"ok": True}


@app.post("/analysis/results/{result_id}/retry")
def retry_analysis_result(result_id: int, request: Request, user: User = Depends(get_current_user)):
    """使用原始问题、代码和数据集快照重新执行，生成一条新的分析记录。"""
    with get_session() as db:
        row = _owned_result(db, result_id, user)
        question = row.question
        code = row.code
        dataset_snapshot_rows = _json_value(row.dataset_json, [])
    if not code:
        raise HTTPException(status_code=400, detail="该结果没有可重新执行的分析代码")
    doc_ids = [str(item.get("dataset_id")) for item in dataset_snapshot_rows
               if isinstance(item, dict) and item.get("dataset_id")]
    if not doc_ids:
        raise HTTPException(status_code=400, detail="该结果没有关联可用数据集")
    try:
        active_datasets = load_dataset_records(user.id, doc_ids)
        started_at = time.perf_counter()
        result = run_analysis_with_retries(active_datasets, question, code)
        result["code"] = result.get("code") or code
        result["datasets"] = dataset_snapshot(active_datasets)
        result["execution_ms"] = int((time.perf_counter() - started_at) * 1000)
        new_id = save_analysis_result(user.id, None, question, result)
    except (ValueError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    auth.log_action(user.id, "retry_analysis_result", "analysis_result", str(result_id),
                    ip=auth._client_ip(request))
    return {"result_id": new_id, **result}


@app.patch("/analysis/results/{result_id}")
def update_analysis_result(result_id: int, payload: dict, request: Request, user: User = Depends(get_current_user)):
    """更新结果的展示标题或收藏状态。"""
    title = payload.get("title")
    favorite = payload.get("is_favorite")
    if title is None and favorite is None:
        raise HTTPException(status_code=400, detail="没有可更新的字段")
    with get_session() as db:
        row = _owned_result(db, result_id, user)
        if title is not None:
            title = str(title).strip()
            if not title:
                raise HTTPException(status_code=400, detail="标题不能为空")
            row.title = title[:255]
        if favorite is not None:
            if not isinstance(favorite, bool):
                raise HTTPException(status_code=400, detail="收藏状态必须是布尔值")
            row.is_favorite = favorite
        db.commit()
        db.refresh(row)
        result = _result_view(row)
    auth.log_action(user.id, "update_analysis_result", "analysis_result", str(result_id),
                    detail=json.dumps({"title": title, "is_favorite": favorite}, ensure_ascii=False),
                    ip=auth._client_ip(request))
    return result


@app.post("/analysis/results/{result_id}/copy")
def copy_analysis_result(result_id: int, request: Request, user: User = Depends(get_current_user)):
    """复制一条结果，保留代码和数据快照，作为新的可编辑记录。"""
    with get_session() as db:
        source = _owned_result(db, result_id, user)
        row = AnalysisResult(
            user_id=user.id,
            session_id=None,
            question=source.question,
            title=f"{source.title or source.question[:80]}（副本）"[:255],
            is_favorite=False,
            code=source.code,
            stdout=source.stdout,
            table_json=source.table_json,
            chart_json=source.chart_json,
            dataset_json=source.dataset_json,
            conclusion=source.conclusion,
            execution_ms=source.execution_ms,
            error_msg=source.error_msg,
            status=source.status,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        result = _result_view(row)
    auth.log_action(user.id, "copy_analysis_result", "analysis_result", str(result_id),
                    ip=auth._client_ip(request))
    return result


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
    if len(dataset_ids) != len(set(dataset_ids)):
        raise HTTPException(status_code=400, detail="数据集不能重复选择")
    if not isinstance(joins, list) or len(joins) < 1:
        raise HTTPException(status_code=400, detail="请配置至少一个关联条件")
    for join in joins:
        if not all(join.get(key) for key in ("left_dataset_id", "right_dataset_id", "left_key", "right_key")):
            raise HTTPException(status_code=400, detail="关联条件不完整")
        if join["left_dataset_id"] not in dataset_ids or join["right_dataset_id"] not in dataset_ids:
            raise HTTPException(status_code=400, detail="关联条件包含未选择的数据集")
    try:
        records = load_dataset_records(user.id, dataset_ids)
        columns_by_id = {item["doc_id"]: set(item["summary"].get("preview_columns", [])) for item in records}
        if len(columns_by_id) != len(set(dataset_ids)):
            raise ValueError("存在无法读取的数据集")
        for join in joins:
            if join["left_key"] not in columns_by_id[join["left_dataset_id"]] or join["right_key"] not in columns_by_id[join["right_dataset_id"]]:
                raise ValueError(f"关联字段不存在：{join['left_key']} / {join['right_key']}")
    except (ValueError, KeyError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=f"关联配置校验失败：{exc}")
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
        merged_ids = {dataset_ids[0]}
        pending_joins = list(joins)
        while pending_joins:
            next_join = next((join for join in pending_joins
                              if join["left_dataset_id"] in merged_ids and join["right_dataset_id"] not in merged_ids), None)
            if next_join is None:
                raise ValueError("关联条件无法按顺序连接，请确保每个新数据集都关联到已加入的数据集")
            merged = merged.merge(
                frames[next_join["right_dataset_id"]],
                left_on=next_join["left_key"], right_on=next_join["right_key"],
                how=next_join.get("how", "left"), suffixes=("", "_关联"),
            )
            merged_ids.add(next_join["right_dataset_id"])
            pending_joins.remove(next_join)
    except (ValueError, KeyError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=f"关联执行失败：{exc}")
    output_path = os.path.join(KB_DIR, f"关联结果_{uuid.uuid4().hex}.csv")
    doc = None
    result_id = None
    try:
        merged.to_csv(output_path, index=False, encoding="utf-8-sig")
        # 先用平台统一读取器回读一次，确保生成文件可被后续分析和知识库使用。
        verified = load_dataframe(output_path, ".csv")
        if len(verified.columns) == 0:
            raise ValueError("关联结果没有可读取的字段")
        doc = kb.register_document(output_path, ".csv", f"{relation.name}_关联结果.csv", user_id=user.id)
        summary = build_summary(merged)
        result = {"table": _table_from_df(merged), "chart": None, "stdout": "关联结果文件已生成，可继续在数据分析或仪表盘中读取。", "error": None}
        if instruction:
            mount = {"doc_id": doc["doc_id"], "path": output_path, "ext": ".csv", "filename": doc["filename"], "summary": summary}
            executed = execute_analysis_instruction([mount], instruction)
            result.update(executed)
            result_id = save_analysis_result(user.id, None, instruction, {**result, "code": result.get("code"), "datasets": [dataset_snapshot([mount])[0]]})
        return {"doc_id": doc["doc_id"], "filename": doc["filename"], "summary": summary, "result_id": result_id, "result": {**result, "result_id": result_id}}
    except Exception as exc:
        if result_id:
            delete_analysis_result(result_id, user.id)
        cleanup_generated_document((doc or {}).get("doc_id") if doc else None, output_path, user.id)
        if isinstance(exc, HTTPException):
            raise
        raise HTTPException(status_code=400, detail=f"关联结果生成失败：{exc}")


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
        merged_ids = {dataset_ids[0]}
        pending_joins = list(joins)
        while pending_joins:
            join = next((item for item in pending_joins
                         if item["left_dataset_id"] in merged_ids and item["right_dataset_id"] not in merged_ids), None)
            if join is None:
                raise ValueError("关联条件无法按顺序连接，请确保每个新数据集都关联到已加入的数据集")
            right_id = join["right_dataset_id"]
            if right_id not in frames:
                raise ValueError("关联条件引用了未选择的数据集")
            frame = frame.merge(
                frames[right_id],
                left_on=join["left_key"], right_on=join["right_key"],
                how=join.get("how", "left"), suffixes=("", f"_{right_id[:6]}"),
            )
            merged_ids.add(right_id)
            pending_joins.remove(join)
    except (ValueError, KeyError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=f"关联预览失败：{exc}")
    return {"relation_id": relation_id, "name": row.name, "rows": len(frame), "table": _table_from_df(frame)}


@app.post("/dashboards/{dashboard_id}/analyze")
def analyze_dashboard(dashboard_id: str, payload: dict, user: User = Depends(get_current_user)):
    instruction = str(payload.get("instruction") or "").strip()
    dataset_ids = payload.get("dataset_ids") or []
    if not instruction or not isinstance(dataset_ids, list) or not dataset_ids:
        raise HTTPException(status_code=400, detail="请选择文件并填写自定义指令")
    if len(dataset_ids) != len(set(dataset_ids)):
        raise HTTPException(status_code=400, detail="文件不能重复选择")
    if len(dataset_ids) > MAX_DATASETS:
        raise HTTPException(status_code=400, detail=f"最多同时分析 {MAX_DATASETS} 个数据集")
    with get_session() as db:
        board = _dashboard_accessible(db, dashboard_id, user.id, write=True)
        if board is None:
            raise HTTPException(status_code=404, detail="仪表盘不存在")
    try:
        mounts = load_dataset_records(user.id, dataset_ids)
    except (ValueError, KeyError, FileNotFoundError) as exc:
        raise HTTPException(status_code=400, detail=f"读取仪表盘文件失败：{exc}")
    try:
        result = execute_analysis_instruction(mounts, instruction)
    except llm.ModelConnectionError as exc:
        raise HTTPException(status_code=503, detail=str(exc))
    except Exception as exc:
        detail = str(exc)
        if "quota" in detail.lower() or "FreeTierOnly" in detail:
            raise HTTPException(status_code=503, detail="模型额度已用完，暂时无法执行自定义指令；请检查模型账户额度")
        raise HTTPException(status_code=503, detail=f"模型服务暂时不可用：{detail[:200]}")
    result_id = save_analysis_result(user.id, None, instruction, {
        **result,
    })
    if not result_id:
        raise HTTPException(status_code=500, detail="分析结果保存失败")
    try:
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
    except Exception:
        delete_analysis_result(result_id, user.id)
        raise HTTPException(status_code=500, detail="仪表盘项目保存失败，分析结果已回滚")
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
        own = db.query(Dashboard).filter(Dashboard.user_id == user.id)
        shared = (db.query(Dashboard).join(DashboardWorkspaceShare, DashboardWorkspaceShare.dashboard_id == Dashboard.id)
                  .join(WorkspaceMember, WorkspaceMember.workspace_id == DashboardWorkspaceShare.workspace_id)
                  .filter(WorkspaceMember.user_id == user.id, Dashboard.user_id != user.id))
        rows = own.union(shared).order_by(Dashboard.updated_at.desc()).all()
        result = []
        for row in rows:
            count = db.query(DashboardItem).filter(DashboardItem.dashboard_id == row.id).count()
            shared_row = db.query(DashboardWorkspaceShare).filter(DashboardWorkspaceShare.dashboard_id == row.id).first()
            result.append({"id": row.id, "name": row.name, "description": row.description, "item_count": count,
                           "owner_id": row.user_id, "read_only": row.user_id != user.id,
                           "workspace_id": shared_row.workspace_id if shared_row else None,
                           "updated_at": row.updated_at.isoformat(timespec="seconds") if row.updated_at else ""})
        return result


@app.get("/dashboards/{dashboard_id}")
def get_dashboard(dashboard_id: str, user: User = Depends(get_current_user)):
    with get_session() as db:
        board = _dashboard_accessible(db, dashboard_id, user.id)
        if board is None:
            raise HTTPException(status_code=404, detail="仪表盘不存在")
        items = db.query(DashboardItem).filter(DashboardItem.dashboard_id == board.id).order_by(DashboardItem.created_at.asc()).all()
        items.sort(key=lambda item: _json_value(item.position_json, {}).get("order", 10**9))
        result_ids = [item.result_id for item in items]
        rows = db.query(AnalysisResult).filter(AnalysisResult.id.in_(result_ids), AnalysisResult.user_id == user.id).all() if result_ids else []
        result_map = {row.id: _result_view(row) for row in rows}
        shared_row = db.query(DashboardWorkspaceShare).filter(DashboardWorkspaceShare.dashboard_id == board.id).first()
        return {"id": board.id, "name": board.name, "description": board.description,
                "owner_id": board.user_id, "read_only": board.user_id != user.id,
                "workspace_id": shared_row.workspace_id if shared_row else None,
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
        board = _dashboard_accessible(db, dashboard_id, user.id, write=True)
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


@app.patch("/dashboards/{dashboard_id}/items/{item_id}")
def update_dashboard_item(dashboard_id: str, item_id: str, payload: dict, user: User = Depends(get_current_user)):
    with get_session() as db:
        board = _dashboard_accessible(db, dashboard_id, user.id, write=True)
        item = db.query(DashboardItem).filter(
            DashboardItem.id == item_id, DashboardItem.dashboard_id == dashboard_id
        ).first()
        if board is None or item is None:
            raise HTTPException(status_code=404, detail="仪表盘项目不存在")
        if "title" in payload:
            title = str(payload.get("title") or "分析图表").strip()
            if not title:
                raise HTTPException(status_code=400, detail="项目标题不能为空")
            item.title = title[:128]
        if "chart_config" in payload:
            item.chart_config_json = json.dumps(payload.get("chart_config") or {}, ensure_ascii=False)
        if "position" in payload:
            item.position_json = json.dumps(payload.get("position") or {}, ensure_ascii=False)
        board.updated_at = datetime.now()
        db.commit()
        return {"ok": True, "id": item.id}


@app.delete("/dashboards/{dashboard_id}")
def delete_dashboard(dashboard_id: str, user: User = Depends(get_current_user)):
    with get_session() as db:
        board = _dashboard_accessible(db, dashboard_id, user.id, write=True)
        if board is None:
            raise HTTPException(status_code=404, detail="仪表盘不存在")
        db.delete(board)
        db.commit()
    return {"ok": True}


@app.delete("/dashboards/{dashboard_id}/items/{item_id}")
def delete_dashboard_item(dashboard_id: str, item_id: str, user: User = Depends(get_current_user)):
    with get_session() as db:
        board = _dashboard_accessible(db, dashboard_id, user.id, write=True)
        item = db.query(DashboardItem).filter(DashboardItem.id == item_id, DashboardItem.dashboard_id == dashboard_id).first()
        if board is None or item is None:
            raise HTTPException(status_code=404, detail="仪表盘项目不存在")
        db.delete(item)
        board.updated_at = datetime.now()
        db.commit()
    return {"ok": True}


@app.put("/dashboards/{dashboard_id}/workspace-share")
def share_dashboard_to_workspace(dashboard_id: str, payload: dict, user: User = Depends(get_current_user)):
    workspace_id = str(payload.get("workspace_id") or "").strip()
    if not workspace_id:
        raise HTTPException(status_code=400, detail="请选择团队")
    with get_session() as db:
        board = db.query(Dashboard).filter(Dashboard.id == dashboard_id, Dashboard.user_id == user.id).first()
        member = _workspace_member(db, workspace_id, user.id)
        if board is None:
            raise HTTPException(status_code=404, detail="仪表盘不存在")
        if member is None or member.role != "owner":
            raise HTTPException(status_code=403, detail="只有团队所有者可以共享仪表盘")
        share = db.query(DashboardWorkspaceShare).filter(DashboardWorkspaceShare.dashboard_id == dashboard_id).first()
        if share is None:
            share = DashboardWorkspaceShare(dashboard_id=dashboard_id, workspace_id=workspace_id, created_by=user.id)
            db.add(share)
        else:
            share.workspace_id = workspace_id
        db.commit()
        return {"dashboard_id": dashboard_id, "workspace_id": workspace_id, "shared": True}


@app.delete("/dashboards/{dashboard_id}/workspace-share")
def unshare_dashboard_from_workspace(dashboard_id: str, user: User = Depends(get_current_user)):
    with get_session() as db:
        board = db.query(Dashboard).filter(Dashboard.id == dashboard_id, Dashboard.user_id == user.id).first()
        if board is None:
            raise HTTPException(status_code=404, detail="仪表盘不存在")
        share = db.query(DashboardWorkspaceShare).filter(DashboardWorkspaceShare.dashboard_id == dashboard_id).first()
        if share:
            db.delete(share)
            db.commit()
        return {"dashboard_id": dashboard_id, "shared": False}


# ———————— 流水线工作流 ————————

WORKFLOW_STEP_TYPES = {"clean", "relation", "analysis", "chart", "dashboard", "report"}


def _workflow_step_view(step: WorkflowStep) -> dict:
    return {
        "id": step.id,
        "step_type": step.step_type,
        "step_order": step.step_order,
        "name": step.name,
        "config": _json_value(step.config_json, {}),
        "enabled": bool(step.enabled),
    }


def _workflow_view(db, workflow: Workflow, include_steps: bool = True) -> dict:
    steps = db.query(WorkflowStep).filter(WorkflowStep.workflow_id == workflow.id).order_by(
        WorkflowStep.step_order.asc(), WorkflowStep.created_at.asc()
    ).all()
    payload = {
        "id": workflow.id,
        "name": workflow.name,
        "description": workflow.description,
        "enabled": bool(workflow.enabled),
        "updated_at": workflow.updated_at.isoformat(timespec="seconds") if workflow.updated_at else "",
        "step_count": len([step for step in steps if step.enabled]),
    }
    if include_steps:
        payload["steps"] = [_workflow_step_view(step) for step in steps]
    return payload


def _owned_workflow(db, workflow_id: str, user: User) -> Workflow:
    workflow = db.query(Workflow).filter(
        Workflow.id == workflow_id, Workflow.user_id == user.id
    ).first()
    if workflow is None:
        raise HTTPException(status_code=404, detail="流水线不存在")
    return workflow


def _workflow_write_file(frame: pd.DataFrame, filename: str, user_id: int) -> dict:
    safe_name = re.sub(r"[^\w\-.\u4e00-\u9fff]", "_", filename)[:100] or "流水线结果"
    output_path = os.path.join(KB_DIR, f"{uuid.uuid4().hex}_{safe_name}.csv")
    frame.to_csv(output_path, index=False, encoding="utf-8-sig")
    doc = kb.register_document(output_path, ".csv", f"{safe_name}.csv", user_id=user_id)
    return {"doc_id": doc["doc_id"], "filename": doc["filename"], "path": output_path, "ext": ".csv",
            "summary": build_summary(frame)}


def _workflow_clean_frame(frame: pd.DataFrame, config: dict) -> pd.DataFrame:
    result = frame.copy()
    operations = config.get("operations") or []
    if config.get("drop_empty_rows"):
        result = result.dropna(how="all")
    if config.get("drop_duplicates"):
        result = result.drop_duplicates()
    for operation in operations:
        op = str(operation.get("op") or "").strip().lower()
        column = operation.get("column")
        if op == "drop_empty_rows":
            result = result.dropna(how="all")
        elif op == "drop_duplicates":
            result = result.drop_duplicates()
        elif op == "fillna" and column in result.columns:
            result[column] = result[column].fillna(operation.get("value", ""))
        elif op == "rename" and column in result.columns:
            target = str(operation.get("value") or "").strip()
            if target:
                result = result.rename(columns={column: target})
        elif op == "drop_column" and column in result.columns:
            result = result.drop(columns=[column])
        elif op == "filter_equals" and column in result.columns:
            result = result[result[column].astype(str) == str(operation.get("value", ""))]
    return result.reset_index(drop=True)


def _workflow_merge_frames(frames: dict, dataset_ids: list, joins: list) -> pd.DataFrame:
    if not dataset_ids or dataset_ids[0] not in frames:
        raise ValueError("流水线关联缺少起始数据集")
    merged = frames[dataset_ids[0]]
    merged_ids = {dataset_ids[0]}
    pending = list(joins)
    if not pending and len(dataset_ids) > 1:
        # 未配置条件时，按相邻表的第一个同名字段自动建立左连接。
        for index in range(1, len(dataset_ids)):
            left_id, right_id = dataset_ids[index - 1], dataset_ids[index]
            common = [str(column) for column in frames[left_id].columns if column in frames[right_id].columns]
            if not common:
                raise ValueError(f"{left_id[:6]} 与 {right_id[:6]} 没有同名字段，请手动配置关联键")
            pending.append({"left_dataset_id": left_id, "right_dataset_id": right_id,
                            "left_key": common[0], "right_key": common[0], "how": "left"})
    while pending:
        join = next((item for item in pending
                     if item.get("left_dataset_id") in merged_ids
                     and item.get("right_dataset_id") not in merged_ids), None)
        if join is None:
            raise ValueError("关联条件无法按顺序连接，请检查每个数据集是否能接入当前结果")
        right_id = join.get("right_dataset_id")
        if right_id not in frames:
            raise ValueError("关联条件引用了未输入的数据集")
        merged = merged.merge(
            frames[right_id], left_on=join.get("left_key"), right_on=join.get("right_key"),
            how=join.get("how", "left"), suffixes=("", f"_{right_id[:6]}"),
        )
        merged_ids.add(right_id)
        pending.remove(join)
    return merged


def _workflow_load_frames(user_id: int, dataset_ids: list) -> tuple[list, dict]:
    records = load_dataset_records(user_id, dataset_ids)
    return records, {record["doc_id"]: load_dataframe(record["path"], record["ext"]) for record in records}


def _workflow_resolve(value, parameters: dict):
    """递归替换步骤配置中的 {{参数名}}，保留非字符串配置类型。"""
    if isinstance(value, dict):
        return {key: _workflow_resolve(item, parameters) for key, item in value.items()}
    if isinstance(value, list):
        return [_workflow_resolve(item, parameters) for item in value]
    if not isinstance(value, str):
        return value
    exact = re.fullmatch(r"\{\{\s*([\w.-]+)\s*\}\}", value)
    if exact and exact.group(1) in parameters:
        return parameters[exact.group(1)]
    return re.sub(r"\{\{\s*([\w.-]+)\s*\}\}",
                  lambda match: str(parameters.get(match.group(1), match.group(0))), value)


def _workflow_report(run_id: str, workflow_name: str, outputs: dict) -> dict:
    report_dir = os.path.join(UPLOAD_DIR, "reports")
    os.makedirs(report_dir, exist_ok=True)
    title = f"{workflow_name} · {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    sections = [f"<h1>{html.escape(title)}</h1>", "<p class='muted'>以下内容由流水线步骤自动生成，可回到系统查看交互式图表。</p>"]
    for key, value in outputs.items():
        sections.append(f"<section><h2>{html.escape(str(key))}</h2>")
        if isinstance(value, dict) and isinstance(value.get("result"), dict):
            result = value["result"]
            if result.get("stdout"):
                sections.append(f"<h3>分析结论</h3><div class='text'>{html.escape(str(result['stdout']))}</div>")
            table = result.get("table")
            if isinstance(table, dict) and table.get("columns"):
                head = "".join(f"<th>{html.escape(str(column))}</th>" for column in table["columns"])
                body = "".join("<tr>" + "".join(f"<td>{html.escape(str(cell))}</td>" for cell in row) + "</tr>" for row in (table.get("rows") or []))
                sections.append(f"<h3>结果表格</h3><table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>")
            if result.get("chart"):
                sections.append(f"<h3>图表配置</h3><pre>{html.escape(json.dumps(result['chart'], ensure_ascii=False, indent=2, default=str))}</pre>")
        sections.append(f"<details><summary>步骤原始数据</summary><pre>{html.escape(json.dumps(value, ensure_ascii=False, indent=2, default=str))}</pre></details></section>")
    path = os.path.join(report_dir, f"workflow_{run_id}.html")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("<!doctype html><meta charset='utf-8'><title>流水线分析报告</title><style>body{font-family:Arial,'Microsoft YaHei',sans-serif;max-width:1100px;margin:32px auto;color:#203c38;line-height:1.6;padding:0 18px}.muted{color:#6f8580}section{border-top:1px solid #dce8e5;padding:18px 0}h1{font-size:24px}h2{font-size:18px}h3{font-size:14px;margin:14px 0 6px}.text,pre{white-space:pre-wrap;background:#f6f9f8;padding:12px;border-radius:6px}pre{overflow:auto}table{border-collapse:collapse;width:100%;font-size:13px}th,td{border:1px solid #dce8e5;padding:7px;text-align:left}th{background:#eef6f4}summary{cursor:pointer;color:#287f76}</style>" + "".join(sections))
    return {"path": path, "filename": os.path.basename(path), "format": "html"}


def _execute_workflow_step(step: WorkflowStep, context: dict, user_id: int, workflow_name: str) -> dict:
    config = _workflow_resolve(_json_value(step.config_json, {}), context.get("parameters", {}))
    step_type = step.step_type
    dataset_ids = list(context.get("dataset_ids") or [])
    if step_type == "clean":
        target_ids = config.get("dataset_ids") or dataset_ids
        records, frames = _workflow_load_frames(user_id, target_ids)
        outputs = []
        for record in records:
            cleaned = _workflow_clean_frame(frames[record["doc_id"]], config)
            outputs.append(_workflow_write_file(cleaned, f"{os.path.splitext(record['filename'])[0]}_清洗结果", user_id))
        context["dataset_ids"] = [item["doc_id"] for item in outputs]
        return {"dataset_ids": context["dataset_ids"], "files": outputs}
    if step_type == "relation":
        target_ids = config.get("dataset_ids") or dataset_ids
        records, frames = _workflow_load_frames(user_id, target_ids)
        joins = config.get("joins") or []
        if not joins and len(target_ids) >= 2 and config.get("left_key") and config.get("right_key"):
            joins = [{"left_dataset_id": target_ids[0], "right_dataset_id": target_ids[1],
                      "left_key": config["left_key"], "right_key": config["right_key"],
                      "how": config.get("how", "left")}]
        merged = _workflow_merge_frames(frames, target_ids, joins)
        output = _workflow_write_file(merged, config.get("name") or "关联结果", user_id)
        context["dataset_ids"] = [output["doc_id"]]
        return output
    if step_type == "analysis":
        target_ids = config.get("dataset_ids") or dataset_ids
        mounts = load_dataset_records(user_id, target_ids)
        instruction = str(config.get("instruction") or "请总结数据中的主要趋势、异常和关键指标")
        result = execute_analysis_instruction(mounts, instruction)
        result_id = save_analysis_result(user_id, None, instruction, result)
        output = {"result_id": result_id, "instruction": instruction, "result": result}
        context["result_id"] = result_id
        context["last_result"] = result
        return output
    if step_type == "chart":
        result = context.get("last_result") or {}
        chart = result.get("chart")
        config_type = config.get("type") or "original"
        if chart and isinstance(chart, dict):
            chart = {**chart, "type": config_type if config_type != "original" else chart.get("type", "bar")}
        output = {"type": config_type, "chart": chart, "result_id": context.get("result_id")}
        context["chart"] = output
        return output
    if step_type == "dashboard":
        dashboard_id = str(config.get("dashboard_id") or "")
        result_id = context.get("result_id")
        if not dashboard_id or not result_id:
            raise ValueError("仪表盘步骤需要 dashboard_id，且前面必须有分析步骤")
        with get_session() as db:
            board = db.query(Dashboard).filter(Dashboard.id == dashboard_id, Dashboard.user_id == user_id).first()
            if board is None:
                raise ValueError("目标仪表盘不存在")
            item = DashboardItem(id=uuid.uuid4().hex, dashboard_id=dashboard_id, result_id=int(result_id),
                                 title=str(config.get("title") or step.name)[:128],
                                 chart_config_json=json.dumps(context.get("chart") or {}, ensure_ascii=False),
                                 position_json=json.dumps({"order": 10**9}, ensure_ascii=False))
            db.add(item)
            board.updated_at = datetime.now()
            db.commit()
        return {"dashboard_id": dashboard_id, "item_id": item.id, "result_id": result_id}
    if step_type == "report":
        return _workflow_report(context["run_id"], workflow_name, context.get("outputs", {}))
    raise ValueError(f"不支持的流水线步骤：{step_type}")


def _execute_workflow(run_id: str, user_id: int, start_order: int = 0) -> dict:
    with get_session() as db:
        run = db.query(WorkflowRun).filter(WorkflowRun.id == run_id, WorkflowRun.user_id == user_id).first()
        if run is None:
            raise ValueError("流水线执行记录不存在")
        workflow = db.query(Workflow).filter(Workflow.id == run.workflow_id, Workflow.user_id == user_id).first()
        if workflow is None:
            raise ValueError("流水线不存在")
        workflow_name = workflow.name
        steps = [
            SimpleNamespace(
                id=step.id,
                step_type=step.step_type,
                step_order=step.step_order,
                name=step.name,
                config_json=step.config_json,
            )
            for step in db.query(WorkflowStep)
            .filter(WorkflowStep.workflow_id == workflow.id, WorkflowStep.enabled.is_(True))
            .order_by(WorkflowStep.step_order.asc())
            .all()
        ]
        context = _json_value(run.output_json, {})
        context.setdefault("dataset_ids", _json_value(run.input_json, {}).get("dataset_ids", []))
        context.setdefault("parameters", _json_value(run.input_json, {}).get("parameters", {}))
        context.setdefault("outputs", {})
        context["run_id"] = run_id
        run.status = "running"
        run.error_msg = None
        db.commit()

    for step in steps:
        if step.step_order < start_order:
            continue
        with get_session() as db:
            run = db.query(WorkflowRun).filter(WorkflowRun.id == run_id, WorkflowRun.user_id == user_id).first()
            if run is None:
                raise ValueError("流水线执行记录不存在")
            run.current_step = step.id
            run_step = db.query(WorkflowRunStep).filter(
                WorkflowRunStep.run_id == run_id, WorkflowRunStep.step_id == step.id
            ).first()
            if run_step is None:
                run_step = WorkflowRunStep(run_id=run_id, step_id=step.id, step_type=step.step_type, step_order=step.step_order)
                db.add(run_step)
            run_step.status = "running"
            run_step.started_at = datetime.now()
            run_step.input_json = json.dumps({"dataset_ids": context.get("dataset_ids", [])}, ensure_ascii=False)
            db.commit()
        try:
            output = _execute_workflow_step(step, context, user_id, workflow_name)
            context["outputs"][step.id] = output
            with get_session() as db:
                run_step = db.query(WorkflowRunStep).filter(WorkflowRunStep.run_id == run_id, WorkflowRunStep.step_id == step.id).first()
                run_step.status = "success"
                run_step.output_json = json.dumps(output, ensure_ascii=False, default=str)
                run_step.finished_at = datetime.now()
                run = db.query(WorkflowRun).filter(WorkflowRun.id == run_id).first()
                run.output_json = json.dumps(context, ensure_ascii=False, default=str)
                db.commit()
        except Exception as exc:
            with get_session() as db:
                run_step = db.query(WorkflowRunStep).filter(WorkflowRunStep.run_id == run_id, WorkflowRunStep.step_id == step.id).first()
                run_step.status = "failed"
                run_step.error_msg = str(exc)[:2000]
                run_step.finished_at = datetime.now()
                run = db.query(WorkflowRun).filter(WorkflowRun.id == run_id).first()
                run.status = "failed"
                run.error_msg = str(exc)[:2000]
                run.output_json = json.dumps(context, ensure_ascii=False, default=str)
                run.finished_at = datetime.now()
                db.commit()
            return {"status": "failed", "error": str(exc), "run_id": run_id, "failed_step": step.id}
    with get_session() as db:
        run = db.query(WorkflowRun).filter(WorkflowRun.id == run_id).first()
        run.status = "success"
        run.current_step = None
        run.output_json = json.dumps(context, ensure_ascii=False, default=str)
        run.finished_at = datetime.now()
        db.commit()
    return {"status": "success", "run_id": run_id, "output": context}


@app.get("/workflows")
def list_workflows(user: User = Depends(get_current_user)):
    with get_session() as db:
        return [_workflow_view(db, row, include_steps=False) for row in db.query(Workflow).filter(Workflow.user_id == user.id).order_by(Workflow.updated_at.desc()).all()]


@app.post("/workflows")
def create_workflow(payload: dict, user: User = Depends(get_current_user)):
    name = str(payload.get("name") or "新流水线").strip()[:128]
    raw_steps = payload.get("steps") or []
    if not isinstance(raw_steps, list) or not raw_steps:
        raise HTTPException(status_code=400, detail="流水线至少需要一个步骤")
    if len(raw_steps) > 20:
        raise HTTPException(status_code=400, detail="流水线最多支持 20 个步骤")
    workflow = Workflow(id=uuid.uuid4().hex, user_id=user.id, name=name, description=str(payload.get("description") or "")[:500])
    with get_session() as db:
        db.add(workflow)
        for index, item in enumerate(raw_steps):
            step_type = str(item.get("step_type") or item.get("type") or "").strip().lower()
            if step_type not in WORKFLOW_STEP_TYPES:
                raise HTTPException(status_code=400, detail=f"不支持的流水线步骤：{step_type}")
            db.add(WorkflowStep(id=uuid.uuid4().hex, workflow_id=workflow.id, step_type=step_type,
                                step_order=index, name=str(item.get("name") or step_type)[:128],
                                config_json=json.dumps(item.get("config") or {}, ensure_ascii=False),
                                enabled=bool(item.get("enabled", True))))
        db.commit()
        return _workflow_view(db, workflow)


@app.get("/workflows/{workflow_id}")
def get_workflow(workflow_id: str, user: User = Depends(get_current_user)):
    with get_session() as db:
        return _workflow_view(db, _owned_workflow(db, workflow_id, user))


@app.patch("/workflows/{workflow_id}")
def update_workflow(workflow_id: str, payload: dict, user: User = Depends(get_current_user)):
    with get_session() as db:
        workflow = _owned_workflow(db, workflow_id, user)
        if "name" in payload:
            workflow.name = str(payload.get("name") or "新流水线").strip()[:128]
        if "description" in payload:
            workflow.description = str(payload.get("description") or "")[:500]
        if "enabled" in payload:
            workflow.enabled = bool(payload["enabled"])
        if "steps" in payload:
            raw_steps = payload["steps"]
            if not isinstance(raw_steps, list) or not raw_steps:
                raise HTTPException(status_code=400, detail="流水线至少需要一个步骤")
            db.query(WorkflowStep).filter(WorkflowStep.workflow_id == workflow_id).delete()
            for index, item in enumerate(raw_steps):
                step_type = str(item.get("step_type") or item.get("type") or "").strip().lower()
                if step_type not in WORKFLOW_STEP_TYPES:
                    raise HTTPException(status_code=400, detail=f"不支持的流水线步骤：{step_type}")
                db.add(WorkflowStep(id=uuid.uuid4().hex, workflow_id=workflow_id, step_type=step_type,
                                    step_order=index, name=str(item.get("name") or step_type)[:128],
                                    config_json=json.dumps(item.get("config") or {}, ensure_ascii=False),
                                    enabled=bool(item.get("enabled", True))))
        workflow.updated_at = datetime.now()
        db.commit()
        return _workflow_view(db, workflow)


@app.delete("/workflows/{workflow_id}")
def delete_workflow(workflow_id: str, user: User = Depends(get_current_user)):
    with get_session() as db:
        workflow = _owned_workflow(db, workflow_id, user)
        db.delete(workflow)
        db.commit()
    return {"ok": True}


@app.post("/workflows/{workflow_id}/run")
def run_workflow(workflow_id: str, payload: dict, user: User = Depends(get_current_user)):
    dataset_ids = payload.get("dataset_ids") or []
    if not isinstance(dataset_ids, list) or len(dataset_ids) > MAX_DATASETS:
        raise HTTPException(status_code=400, detail=f"数据集必须是列表且最多 {MAX_DATASETS} 个")
    if dataset_ids:
        try:
            load_dataset_records(user.id, dataset_ids)
        except (ValueError, KeyError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=f"流水线输入文件无效：{exc}")
    parameters = payload.get("parameters") or {}
    if not isinstance(parameters, dict):
        raise HTTPException(status_code=400, detail="流水线参数必须是对象")
    if len(parameters) > 50:
        raise HTTPException(status_code=400, detail="流水线参数最多 50 项")
    with get_session() as db:
        workflow = _owned_workflow(db, workflow_id, user)
        run = WorkflowRun(id=uuid.uuid4().hex, workflow_id=workflow.id, user_id=user.id,
                          input_json=json.dumps({"dataset_ids": dataset_ids, "parameters": parameters}, ensure_ascii=False))
        db.add(run)
        db.commit()
        run_id = run.id
    return _execute_workflow(run_id, user.id)


@app.get("/workflows/{workflow_id}/runs")
def list_workflow_runs(workflow_id: str, user: User = Depends(get_current_user)):
    with get_session() as db:
        _owned_workflow(db, workflow_id, user)
        runs = db.query(WorkflowRun).filter(WorkflowRun.workflow_id == workflow_id, WorkflowRun.user_id == user.id).order_by(WorkflowRun.started_at.desc()).limit(50).all()
        return [{"id": row.id, "status": row.status, "current_step": row.current_step, "error": row.error_msg,
                 "started_at": row.started_at.isoformat(timespec="seconds") if row.started_at else "",
                 "finished_at": row.finished_at.isoformat(timespec="seconds") if row.finished_at else None,
                 "output": _json_value(row.output_json, {})} for row in runs]


@app.get("/workflow-runs/{run_id}")
def get_workflow_run(run_id: str, user: User = Depends(get_current_user)):
    with get_session() as db:
        run = db.query(WorkflowRun).filter(WorkflowRun.id == run_id, WorkflowRun.user_id == user.id).first()
        if run is None:
            raise HTTPException(status_code=404, detail="流水线执行记录不存在")
        steps = db.query(WorkflowRunStep).filter(WorkflowRunStep.run_id == run_id).order_by(WorkflowRunStep.step_order.asc()).all()
        return {"id": run.id, "status": run.status, "error": run.error_msg,
                "output": _json_value(run.output_json, {}),
                "steps": [{"step_id": item.step_id, "step_type": item.step_type, "status": item.status,
                           "output": _json_value(item.output_json, {}), "error": item.error_msg,
                           "started_at": item.started_at.isoformat(timespec="seconds") if item.started_at else None,
                           "finished_at": item.finished_at.isoformat(timespec="seconds") if item.finished_at else None} for item in steps]}


@app.get("/workflow-runs/{run_id}/report")
def download_workflow_report(run_id: str, user: User = Depends(get_current_user)):
    with get_session() as db:
        run = db.query(WorkflowRun).filter(WorkflowRun.id == run_id, WorkflowRun.user_id == user.id).first()
        if run is None:
            raise HTTPException(status_code=404, detail="流水线执行记录不存在")
        outputs = _json_value(run.output_json, {}).get("outputs", {})
    report = next((value for value in outputs.values() if isinstance(value, dict) and value.get("format") == "html"), None)
    if not report:
        raise HTTPException(status_code=404, detail="该执行记录没有报告")
    report_dir = os.path.abspath(os.path.join(UPLOAD_DIR, "reports"))
    path = os.path.abspath(str(report.get("path") or ""))
    if os.path.commonpath([report_dir, path]) != report_dir or not os.path.isfile(path):
        raise HTTPException(status_code=404, detail="报告文件不存在")
    return FileResponse(path, filename=os.path.basename(path), media_type="text/html")


@app.post("/workflow-runs/{run_id}/retry")
def retry_workflow_run(run_id: str, user: User = Depends(get_current_user)):
    with get_session() as db:
        run = db.query(WorkflowRun).filter(WorkflowRun.id == run_id, WorkflowRun.user_id == user.id).first()
        if run is None:
            raise HTTPException(status_code=404, detail="流水线执行记录不存在")
        failed = db.query(WorkflowRunStep).filter(WorkflowRunStep.run_id == run_id, WorkflowRunStep.status == "failed").order_by(WorkflowRunStep.step_order.asc()).first()
        if failed is None:
            raise HTTPException(status_code=400, detail="没有可重试的失败步骤")
        start_order = failed.step_order
    return _execute_workflow(run_id, user.id, start_order=start_order)


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
        expires_days = payload.get("expires_days")
        expires_at = None
        if expires_days not in (None, "", 0, "0"):
            try:
                expires_days = max(1, min(int(expires_days), 365))
            except (TypeError, ValueError):
                raise HTTPException(status_code=400, detail="分享有效期必须是 1-365 天")
            expires_at = datetime.now() + __import__("datetime").timedelta(days=expires_days)
        raw_password = str(payload.get("password") or "").strip()
        if len(raw_password) > 128:
            raise HTTPException(status_code=400, detail="分享密码不能超过 128 个字符")
        db.add(AnalysisShare(token=token, user_id=user.id, result_id=int(result_id) if result_id else None,
                             dashboard_id=dashboard_id, allow_comments=bool(payload.get("allow_comments", True)),
                             password_hash=hash_password(raw_password) if raw_password else None,
                             allow_download=bool(payload.get("allow_download", True)),
                             expires_at=expires_at))
        db.commit()
    return {"token": token, "path": f"/shared/{token}", "expires_at": expires_at.isoformat(timespec="seconds") if expires_at else None}


@app.delete("/shares/{token}")
def revoke_share(token: str, user: User = Depends(get_current_user)):
    with get_session() as db:
        share = db.query(AnalysisShare).filter(
            AnalysisShare.token == token, AnalysisShare.user_id == user.id
        ).first()
        if share is None:
            raise HTTPException(status_code=404, detail="分享链接不存在")
        db.delete(share)
        db.commit()
    return {"ok": True}


@app.delete("/shares/{token}/comments/{comment_id}")
def delete_share_comment(token: str, comment_id: int, user: User = Depends(get_current_user)):
    with get_session() as db:
        share = db.query(AnalysisShare).filter(
            AnalysisShare.token == token, AnalysisShare.user_id == user.id
        ).first()
        comment = db.query(AnalysisComment).filter(
            AnalysisComment.id == comment_id, AnalysisComment.token == token
        ).first()
        if share is None or comment is None:
            raise HTTPException(status_code=404, detail="评论不存在")
        db.delete(comment)
        db.commit()
    return {"ok": True}


def _public_share_payload(db, share: AnalysisShare) -> dict:
    payload = {"token": share.token, "allow_comments": share.allow_comments,
               "allow_download": share.allow_download,
               "created_at": share.created_at.isoformat(timespec="seconds") if share.created_at else "",
               "expires_at": share.expires_at.isoformat(timespec="seconds") if share.expires_at else None}
    if share.result_id:
        row = db.get(AnalysisResult, share.result_id)
        result = _result_view(row) if row else None
        if result:
            result["table"] = _mask_sensitive_table(result.get("table"))
        payload["result"] = result
    if share.dashboard_id:
        board = db.get(Dashboard, share.dashboard_id)
        payload["dashboard"] = {"id": board.id, "name": board.name, "description": board.description} if board else None
        items = db.query(DashboardItem).filter(DashboardItem.dashboard_id == share.dashboard_id).all()
        result_ids = [item.result_id for item in items]
        rows = db.query(AnalysisResult).filter(
            AnalysisResult.id.in_(result_ids), AnalysisResult.user_id == share.user_id
        ).all() if result_ids else []
        result_map = {}
        for row in rows:
            result = _result_view(row)
            result["table"] = _mask_sensitive_table(result.get("table"))
            result_map[row.id] = result
        payload["items"] = [{"id": item.id, "title": item.title, "chart_config": _json_value(item.chart_config_json, {}),
                             "result": result_map.get(item.result_id)} for item in items]
    payload["comments"] = [{"id": row.id, "author_name": row.author_name, "content": row.content,
                             "created_at": row.created_at.isoformat(timespec="seconds") if row.created_at else ""}
                            for row in db.query(AnalysisComment).filter(AnalysisComment.token == share.token).order_by(AnalysisComment.created_at.asc()).all()]
    return payload


@app.get("/shared/{token}")
def get_shared(token: str, request: Request, password: Optional[str] = None):
    with get_session() as db:
        share = db.get(AnalysisShare, token)
        if share is None or (share.expires_at and share.expires_at < datetime.now()):
            raise HTTPException(status_code=404, detail="分享链接不存在或已过期")
        if share.password_hash and not verify_password(password or "", share.password_hash):
            auth.log_action(None, "share_access_denied", "share", token, ip=auth._client_ip(request))
            raise HTTPException(status_code=401, detail="分享链接需要密码")
        share.access_count = int(share.access_count or 0) + 1
        share.last_access_at = datetime.now()
        db.commit()
        auth.log_action(None, "share_access", "share", token, ip=auth._client_ip(request))
        return _public_share_payload(db, share)


@app.post("/shared/{token}/comments")
def add_shared_comment(token: str, payload: dict, request: Request, password: Optional[str] = None):
    content = str(payload.get("content") or "").strip()
    if not content:
        raise HTTPException(status_code=400, detail="评论不能为空")
    with get_session() as db:
        share = db.get(AnalysisShare, token)
        if share is None or (share.expires_at and share.expires_at < datetime.now()) or not share.allow_comments:
            raise HTTPException(status_code=404, detail="分享链接不存在或不允许评论")
        if share.password_hash and not verify_password(password or "", share.password_hash):
            auth.log_action(None, "share_comment_denied", "share", token, ip=auth._client_ip(request))
            raise HTTPException(status_code=401, detail="分享链接需要密码")
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


def _send_schedule_notification(job: ScheduleJob, output: dict) -> dict:
    """发送定时报告；未配置凭据时保留可追踪的模拟发送记录。"""
    config = _json_value(job.parameters_json, {})
    channel = str(config.get("notification_channel") or "email").lower()
    recipients = _json_value(job.recipients_json, [])
    subject = str(config.get("report_template") or job.name)
    body = json.dumps(output, ensure_ascii=False, indent=2)
    if channel == "email" and os.getenv("SMTP_HOST") and recipients:
        message = EmailMessage()
        message["Subject"] = subject
        message["From"] = os.getenv("SMTP_FROM") or os.getenv("SMTP_USER") or "ai-data-analysis@localhost"
        message["To"] = ", ".join(recipients)
        message.set_content(body)
        with smtplib.SMTP(os.getenv("SMTP_HOST"), int(os.getenv("SMTP_PORT", "587")), timeout=20) as client:
            if os.getenv("SMTP_TLS", "true").lower() == "true":
                client.starttls()
            if os.getenv("SMTP_USER"):
                client.login(os.getenv("SMTP_USER"), os.getenv("SMTP_PASSWORD", ""))
            client.send_message(message)
        return {"channel": channel, "status": "sent", "simulated": False, "recipients": recipients}
    webhook = str(config.get("webhook_url") or "").strip()
    if channel in {"webhook", "feishu", "dingtalk", "wecom"} and webhook:
        payload = {"event": "report", "job": job.name, "output": output}
        if channel == "feishu":
            payload = {"msg_type": "text", "content": {"text": body}}
        elif channel in {"dingtalk", "wecom"}:
            payload = {"msgtype": "text", "text": {"content": body}}
        request = UrlRequest(webhook, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"), headers={"Content-Type": "application/json"}, method="POST")
        with urlopen(request, timeout=20) as response:
            if response.status >= 300:
                raise RuntimeError(f"通知接口返回 HTTP {response.status}")
        return {"channel": channel, "status": "sent", "simulated": False, "recipients": recipients}
    return {"channel": channel, "status": "simulated", "simulated": True, "recipients": recipients, "reason": "未配置发送凭据"}


def _claim_schedule(job_id: str, user_id: int) -> str:
    token = uuid.uuid4().hex
    now = datetime.now()
    stale_before = now - __import__("datetime").timedelta(minutes=10)
    with get_session() as db:
        changed = db.query(ScheduleJob).filter(
            ScheduleJob.id == job_id,
            ScheduleJob.user_id == user_id,
            (ScheduleJob.locked_at.is_(None) | (ScheduleJob.locked_at < stale_before)),
        ).update({"locked_at": now, "lock_token": token}, synchronize_session=False)
        db.commit()
    if changed != 1:
        raise ValueError("任务正在执行，请稍后再试")
    return token


def _release_schedule(job_id: str, user_id: int, token: str) -> None:
    with get_session() as db:
        db.query(ScheduleJob).filter(
            ScheduleJob.id == job_id, ScheduleJob.user_id == user_id, ScheduleJob.lock_token == token
        ).update({"locked_at": None, "lock_token": None}, synchronize_session=False)
        db.commit()


def _execute_schedule(job_id: str, user_id: int) -> dict:
    lock_token = _claim_schedule(job_id, user_id)
    try:
        return _execute_schedule_locked(job_id, user_id, lock_token)
    finally:
        _release_schedule(job_id, user_id, lock_token)


def _execute_schedule_locked(job_id: str, user_id: int, lock_token: str) -> dict:
    with get_session() as db:
        job = db.query(ScheduleJob).filter(ScheduleJob.id == job_id, ScheduleJob.user_id == user_id).first()
        if job is None:
            raise ValueError("调度任务不存在")
        parameters = _json_value(job.parameters_json, {})
        output = {"job": job.name, "type": job.job_type, "simulated_email": True,
                  "report_template": parameters.get("report_template") or "经营分析报告",
                  "recipients": _json_value(job.recipients_json, []), "executed_at": datetime.now().isoformat(timespec="seconds")}
        error = None
        try:
            if job.job_type == "sync":
                sync_config = {**parameters, "url": parameters.get("url") or parameters.get("source_url")}
                filename, content, _ = data_sources.fetch_remote_dataset(sync_config)
                target = os.path.join(KB_DIR, f"{uuid.uuid4().hex}.csv")
                with open(target, "wb") as dst:
                    dst.write(content)
                df = load_dataframe(target, ".csv")
                doc = kb.register_document(target, ".csv", filename, user_id=user_id)
                summary = build_summary(df)
                mount_dataset(user_id, doc["doc_id"], {"path": target, "ext": ".csv", "filename": filename, "summary": summary, "doc_id": doc["doc_id"]})
                output["synced"] = {"dataset_id": doc["doc_id"], "filename": filename, "rows": summary["rows"], "cols": summary["cols"]}
            elif job.job_type == "upload":
                source = os.path.abspath(job.source_path or "")
                ext = os.path.splitext(source)[1].lower()
                if not os.path.isfile(source) or ext not in kb.KB_EXTENSIONS:
                    raise ValueError("定时上传源文件不存在或类型不支持")
                target = os.path.join(KB_DIR, f"{uuid.uuid4().hex}{ext}")
                with open(source, "rb") as src, open(target, "wb") as dst:
                    dst.write(src.read())
                doc = kb.register_document(target, ext, os.path.basename(source), user_id=user_id)
                output["uploaded"] = doc
            elif job.job_type == "workflow":
                workflow = db.query(Workflow).filter(
                    Workflow.id == job.workflow_id, Workflow.user_id == user_id
                ).first()
                if workflow is None:
                    raise ValueError("定时流水线不存在或已删除")
                workflow_run = WorkflowRun(
                    id=uuid.uuid4().hex, workflow_id=workflow.id, user_id=user_id,
                    input_json=json.dumps({
                        "dataset_ids": _json_value(job.dataset_ids_json, []),
                        "parameters": _json_value(job.parameters_json, {}),
                    }, ensure_ascii=False),
                )
                db.add(workflow_run)
                db.commit()
                output["workflow_id"] = workflow.id
                output["workflow_run"] = _execute_workflow(workflow_run.id, user_id)
            else:
                question = job.question or "定时分析报告"
                mounts = load_dataset_records(user_id, _json_value(job.dataset_ids_json, []))
                result = execute_analysis_instruction(mounts, question)
                result_id = save_analysis_result(user_id, None, question, result)
                output["question"] = question
                output["result_id"] = result_id
                output["result"] = result
                output["message"] = "分析已执行；邮件发送仅保留模拟记录。"
        except Exception as exc:
            error = str(exc)
        if error:
            try:
                output["notification"] = _send_schedule_notification(job, {**output, "status": "failed", "error": error})
                output["simulated_email"] = bool(output["notification"].get("simulated"))
            except Exception as notification_exc:
                output["notification"] = {"status": "failed", "error": str(notification_exc)}
        else:
            try:
                output["notification"] = _send_schedule_notification(job, output)
                output["simulated_email"] = bool(output["notification"].get("simulated"))
            except Exception as exc:
                error = f"通知发送失败：{exc}"
                output["notification"] = {"status": "failed", "error": str(exc)}
        run = ScheduleRun(job_id=job_id, status="failed" if error else "success", simulated_email=bool(output.get("simulated_email")),
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
                 "workflow_id": row.workflow_id, "parameters": _json_value(row.parameters_json, {}),
                 "recipients": _json_value(row.recipients_json, []), "enabled": row.enabled,
                 "last_run_at": row.last_run_at.isoformat(timespec="seconds") if row.last_run_at else None,
                  "next_run_at": row.next_run_at.isoformat(timespec="seconds") if row.next_run_at else None} for row in rows]


@app.get("/schedules/{job_id}/runs")
def list_schedule_runs(job_id: str, user: User = Depends(get_current_user)):
    with get_session() as db:
        job = db.query(ScheduleJob).filter(ScheduleJob.id == job_id, ScheduleJob.user_id == user.id).first()
        if job is None:
            raise HTTPException(status_code=404, detail="调度任务不存在")
        rows = db.query(ScheduleRun).filter(ScheduleRun.job_id == job_id).order_by(ScheduleRun.created_at.desc()).limit(50).all()
        return [{"id": row.id, "status": row.status, "simulated_email": row.simulated_email,
                 "output": _json_value(row.output_json, {}), "error": row.error_msg,
                 "created_at": row.created_at.isoformat(timespec="seconds") if row.created_at else ""} for row in rows]


@app.post("/schedules/{job_id}/runs/{run_id}/retry")
def retry_schedule_run(job_id: str, run_id: int, user: User = Depends(get_current_user)):
    with get_session() as db:
        run = db.query(ScheduleRun).join(ScheduleJob, ScheduleJob.id == ScheduleRun.job_id).filter(
            ScheduleRun.id == run_id, ScheduleRun.job_id == job_id, ScheduleJob.user_id == user.id
        ).first()
        if run is None:
            raise HTTPException(status_code=404, detail="调度执行记录不存在")
        if run.status != "failed":
            raise HTTPException(status_code=409, detail="只有失败记录可以重试")
    return _execute_schedule(job_id, user.id)


@app.post("/schedules")
def create_schedule(payload: dict, user: User = Depends(get_current_user)):
    try:
        next_run = _next_schedule(str(payload.get("schedule_text") or ""), datetime.now())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    job_type = str(payload.get("job_type") or "analysis")
    dataset_ids = payload.get("dataset_ids") or []
    if job_type == "analysis":
        if not isinstance(dataset_ids, list) or not dataset_ids:
            raise HTTPException(status_code=400, detail="定时分析至少需要一个数据集")
        try:
            load_dataset_records(user.id, dataset_ids)
        except (ValueError, KeyError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=f"定时分析数据集无效：{exc}")
    workflow_id = str(payload.get("workflow_id") or "").strip() or None
    parameters = payload.get("parameters") or {}
    if not isinstance(parameters, dict):
        raise HTTPException(status_code=400, detail="任务参数必须是对象")
    parameters = dict(parameters)
    parameters["report_template"] = str(payload.get("report_template") or parameters.get("report_template") or "经营分析报告")[:64]
    parameters["notification_channel"] = str(payload.get("notification_channel") or parameters.get("notification_channel") or "email")[:32]
    if payload.get("webhook_url"):
        parameters["webhook_url"] = str(payload.get("webhook_url"))[:1024]
    if payload.get("source_url"):
        parameters["source_url"] = str(payload.get("source_url"))[:2048]
    if job_type == "sync" and not (parameters.get("source_url") or parameters.get("url")):
        raise HTTPException(status_code=400, detail="定时同步需要 API 或 Webhook 地址")
    if job_type == "workflow":
        if not workflow_id:
            raise HTTPException(status_code=400, detail="请选择要定时执行的流水线")
        with get_session() as db:
            if not db.query(Workflow).filter(Workflow.id == workflow_id, Workflow.user_id == user.id).first():
                raise HTTPException(status_code=404, detail="流水线不存在")
        if not isinstance(dataset_ids, list) or not dataset_ids:
            raise HTTPException(status_code=400, detail="定时流水线至少需要一个输入文件")
        try:
            load_dataset_records(user.id, dataset_ids)
        except (ValueError, KeyError, FileNotFoundError) as exc:
            raise HTTPException(status_code=400, detail=f"定时流水线输入无效：{exc}")
        if not isinstance(parameters, dict):
            raise HTTPException(status_code=400, detail="流水线参数必须是对象")
    recipients = [str(item).strip()[:254] for item in (payload.get("recipients") or []) if str(item).strip()]
    if len(recipients) > 20:
        raise HTTPException(status_code=400, detail="模拟收件人最多 20 个")
    job = ScheduleJob(id=uuid.uuid4().hex, user_id=user.id, name=str(payload.get("name") or "定时任务")[:128],
                      job_type=job_type, schedule_text=str(payload.get("schedule_text")),
                      dataset_ids_json=json.dumps(dataset_ids, ensure_ascii=False),
                      question=str(payload.get("question") or "")[:2000], source_path=str(payload.get("source_path") or "")[:512],
                      workflow_id=workflow_id, parameters_json=json.dumps(parameters, ensure_ascii=False),
                      recipients_json=json.dumps(recipients, ensure_ascii=False), next_run_at=next_run)
    if job.job_type not in ("analysis", "upload", "workflow", "sync"):
        raise HTTPException(status_code=400, detail="任务类型只支持 analysis、upload、workflow 或 sync")
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
                "result_id": item.id,
                "question": item.question,
                "title": item.title or item.question[:80],
                "is_favorite": item.is_favorite,
                "code": item.code,
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
try:
    _pending = kb.resume_pending_ingest()
    if _pending:
        print(f"[startup] resumed {_pending} pending knowledge-base jobs", file=sys.stderr)
except Exception as e:
    print(f"[startup] pending knowledge-base recovery skipped: {e}", file=sys.stderr)
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
