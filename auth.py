"""鉴权模块：服务端 session + cookie + bcrypt 密码校验。

组件：
- password_utils: 密码哈希/校验（bcrypt 直接调用）
- SessionMiddleware（在 main.py 注册）：cookie 签名存储 user_id
- get_current_user：FastAPI 依赖，从 request.session 取 user_id 查库
- APIRouter /auth/*：login / register / logout / me

设计：
- cookie 用 itsdangerous 签名（不可篡改），内容只放 user_id 和 username（避免每次请求查库）
- 401 不在依赖里直接抛，而是返回 None，路由自行判断（便于根路由 / 不强制登录）
- 注册接口开放（无鉴权依赖），登录接口也开放
"""
import os
import secrets
import sys
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from db import AuditLog, User, get_session
from password_utils import hash_password, verify_password

router = APIRouter(prefix="/auth", tags=["auth"])

# 会话 cookie 有效期：30 天（单位秒，配合 SessionMiddleware 的 max_age）
SESSION_MAX_AGE = 30 * 24 * 3600


# ==================== Pydantic 模型 ====================

class LoginIn(BaseModel):
    username: str = Field(..., min_length=1, max_length=64)
    password: str = Field(..., min_length=1, max_length=128)


class RegisterIn(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    password: str = Field(..., min_length=6, max_length=128)
    display_name: str | None = Field(None, max_length=64)


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str | None
    role: str


# ==================== 依赖：当前用户 ====================

def _set_session(request: Request, user: User) -> None:
    """把用户基本信息写入 session（仅 id 和 username，role 实时查库防伪造）"""
    request.session["user_id"] = user.id
    request.session["username"] = user.username


def get_current_user(request: Request) -> User:
    """FastAPI 依赖：从 session 取 user_id 查库，未登录抛 401"""
    user_id = request.session.get("user_id")
    if not user_id:
        raise HTTPException(status_code=401, detail="未登录或会话已过期，请重新登录")
    with get_session() as db:
        user = db.get(User, user_id)
    if user is None:
        # session 中有 user_id 但库里查不到（用户被删/数据库重置）
        request.session.clear()
        raise HTTPException(status_code=401, detail="用户不存在，请重新登录")
    return user


def require_admin(user: User = Depends(get_current_user)) -> User:
    """叠加依赖：仅 admin 角色可访问"""
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


# ==================== 路由 ====================

@router.post("/login", response_model=UserOut)
def login(payload: LoginIn, request: Request):
    """用户名 + 密码登录，成功后写入 session cookie"""
    with get_session() as db:
        user = db.query(User).filter(User.username == payload.username).first()
    if user is None or not verify_password(payload.password, user.password_hash):
        # 用户名/密码错误用同一提示避免账号枚举
        raise HTTPException(status_code=401, detail="用户名或密码错误")
    _set_session(request, user)
    log_action(user.id, "login", ip=_client_ip(request))
    return user


@router.post("/register", response_model=UserOut, status_code=201)
def register(payload: RegisterIn, request: Request):
    """注册新用户（用户名唯一），成功后自动登录"""
    with get_session() as db:
        existing = db.query(User).filter(User.username == payload.username).first()
        if existing is not None:
            raise HTTPException(status_code=409, detail="用户名已被占用")
        user = User(
            username=payload.username,
            password_hash=hash_password(payload.password),
            display_name=payload.display_name,
            role="user",  # 注册的用户默认非 admin
        )
        db.add(user)
        db.commit()
        db.refresh(user)
    _set_session(request, user)
    return user


@router.post("/logout")
def logout(request: Request):
    """登出：清空 session cookie"""
    uid = request.session.get("user_id")
    request.session.clear()
    if uid:
        log_action(uid, "logout", ip=_client_ip(request))
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    """返回当前登录用户（前端启动时调用判断登录态）"""
    return user


# ==================== 审计日志工具 ====================

def log_action(user_id: Optional[int], action: str,
               target_type: str = None, target_id: str = None,
               detail: str = None, ip: str = None) -> None:
    """记录一条审计日志（失败不抛异常，避免业务流程被打断）"""
    try:
        with get_session() as db:
            db.add(AuditLog(
                user_id=user_id, action=action,
                target_type=target_type, target_id=target_id,
                detail=detail, ip_address=ip,
            ))
            db.commit()
    except Exception as e:
        print(f"audit log failed: {type(e).__name__}: {e}", file=sys.stderr)


def _client_ip(request: Request) -> str:
    """提取客户端 IP（兼容反代场景）"""
    fwd = request.headers.get("x-forwarded-for")
    if fwd:
        return fwd.split(",")[0].strip()
    return request.client.host if request.client else None


# ==================== 管理员：用户管理路由 ====================

class UserCreateIn(BaseModel):
    username: str = Field(..., min_length=2, max_length=64)
    password: str = Field(..., min_length=6, max_length=128)
    display_name: str | None = Field(None, max_length=64)
    role: str = Field("user", pattern="^(user|admin)$")


class PasswordResetIn(BaseModel):
    new_password: str = Field(..., min_length=6, max_length=128)


class UserManageOut(BaseModel):
    id: int
    username: str
    display_name: str | None
    role: str
    created_at: str | None
    updated_at: str | None


@router.get("/admin/users", response_model=list[UserManageOut])
def admin_list_users(admin: User = Depends(require_admin)):
    """列出所有用户（仅 admin）"""
    with get_session() as db:
        rows = db.query(User).order_by(User.id.asc()).all()
        return [
            UserManageOut(
                id=u.id, username=u.username, display_name=u.display_name,
                role=u.role,
                created_at=u.created_at.isoformat(timespec="seconds") if u.created_at else None,
                updated_at=u.updated_at.isoformat(timespec="seconds") if u.updated_at else None,
            )
            for u in rows
        ]


@router.post("/admin/users", response_model=UserManageOut, status_code=201)
def admin_create_user(payload: UserCreateIn, request: Request,
                      admin: User = Depends(require_admin)):
    """管理员创建新用户（不自动登录，需用户自己登录）"""
    with get_session() as db:
        if db.query(User).filter(User.username == payload.username).first():
            raise HTTPException(status_code=409, detail="用户名已被占用")
        u = User(
            username=payload.username,
            password_hash=hash_password(payload.password),
            display_name=payload.display_name,
            role=payload.role,
        )
        db.add(u)
        db.commit()
        db.refresh(u)
        new_id = u.id
        new_username = u.username
    log_action(admin.id, "create_user", "user", str(new_id),
               detail=f"created user {new_username} role={payload.role}",
               ip=_client_ip(request))
    return UserManageOut(
        id=u.id, username=u.username, display_name=u.display_name, role=u.role,
        created_at=u.created_at.isoformat(timespec="seconds") if u.created_at else None,
        updated_at=u.updated_at.isoformat(timespec="seconds") if u.updated_at else None,
    )


@router.patch("/admin/users/{user_id}/password")
def admin_reset_password(user_id: int, payload: PasswordResetIn, request: Request,
                          admin: User = Depends(require_admin)):
    """管理员重置任意用户密码"""
    with get_session() as db:
        u = db.get(User, user_id)
        if u is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        if u.id == admin.id:
            raise HTTPException(status_code=400, detail="不能通过此接口修改自己的密码")
        u.password_hash = hash_password(payload.new_password)
        db.commit()
        target_name = u.username
    log_action(admin.id, "reset_password", "user", str(user_id),
               detail=f"reset password of {target_name}", ip=_client_ip(request))
    return {"ok": True}


@router.delete("/admin/users/{user_id}")
def admin_delete_user(user_id: int, request: Request,
                       admin: User = Depends(require_admin)):
    """删除用户（连带删除其全部数据：文件夹/文档/会话/消息/分析结果，CASCADE）。
    自删保护：admin 不能删自己。"""
    if user_id == admin.id:
        raise HTTPException(status_code=400, detail="不能删除自己")
    with get_session() as db:
        u = db.get(User, user_id)
        if u is None:
            raise HTTPException(status_code=404, detail="用户不存在")
        if u.username == "admin":
            raise HTTPException(status_code=400, detail="不能删除内置 admin 账户")
        target_name = u.username
        db.delete(u)
        db.commit()
    log_action(admin.id, "delete_user", "user", str(user_id),
               detail=f"deleted user {target_name}", ip=_client_ip(request))
    return {"ok": True}


# ==================== 管理员：审计日志查询 ====================

@router.get("/admin/audit-logs")
def admin_list_audit_logs(limit: int = 200, offset: int = 0,
                           admin: User = Depends(require_admin)):
    """查询审计日志（按时间倒序，默认最近 200 条）"""
    if limit > 1000:
        limit = 1000
    with get_session() as db:
        rows = (db.query(AuditLog)
                .order_by(AuditLog.id.desc())
                .offset(offset)
                .limit(limit)
                .all())
        # 关联 username（user_id 可能已为 NULL，用 left join 思路：先查所有用户名
        user_ids = {r.user_id for r in rows if r.user_id}
        user_map = {}
        if user_ids:
            users = db.query(User).filter(User.id.in_(user_ids)).all()
            user_map = {u.id: u.username for u in users}
        return [
            {
                "id": r.id,
                "user_id": r.user_id,
                "username": user_map.get(r.user_id, ""),
                "action": r.action,
                "target_type": r.target_type,
                "target_id": r.target_id,
                "detail": r.detail,
                "ip_address": r.ip_address,
                "created_at": r.created_at.isoformat(timespec="seconds") if r.created_at else "",
            }
            for r in rows
        ]
