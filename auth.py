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

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select

from db import User, get_session
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
    request.session.clear()
    return {"ok": True}


@router.get("/me", response_model=UserOut)
def me(user: User = Depends(get_current_user)):
    """返回当前登录用户（前端启动时调用判断登录态）"""
    return user
