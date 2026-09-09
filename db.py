"""数据库访问层：SQLAlchemy ORM 模型 + 会话管理。

所有表通过 SQLAlchemy 2.0 声明式映射定义，连接串从 .env 的 DATABASE_URL 读取。
当前阶段为单用户模式：所有数据归属到默认用户（id=1），后续接入登录后按 user_id 隔离。
"""
import os
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from sqlalchemy import (
    BigInteger, Boolean, DateTime, ForeignKey, Integer, JSON, String, Text,
    create_engine,
)
from sqlalchemy.orm import (
    DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker,
)

from password_utils import hash_password

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./data_analysis.db")
if not DATABASE_URL:
    raise RuntimeError(
        "未读取到 DATABASE_URL，请检查项目目录下的 .env 文件（参考 .env.example）"
    )

engine = create_engine(
    DATABASE_URL,
    pool_pre_ping=True,        # 连接池自动检测断开的连接
    pool_recycle=3600,         # 1 小时回收连接（MySQL 默认 wait_timeout=8h）
    echo=False,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

# SQLite only auto-increments a column declared exactly as INTEGER PRIMARY KEY.
# Keep BIGINT on MySQL while using INTEGER for local SQLite databases.
AUTO_ID_TYPE = BigInteger().with_variant(Integer, "sqlite")


class Base(DeclarativeBase):
    pass


# ==================== ORM 模型 ====================

class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(AUTO_ID_TYPE, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(64), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    role: Mapped[str] = mapped_column(String(16), default="user", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class KbFolder(Base):
    __tablename__ = "kb_folders"
    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    is_system: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class KbDocument(Base):
    __tablename__ = "kb_documents"
    doc_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    folder_id: Mapped[Optional[str]] = mapped_column(String(32), ForeignKey("kb_folders.id", ondelete="SET NULL"), nullable=True)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_ext: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    file_size: Mapped[int] = mapped_column(BigInteger, default=0)
    status: Mapped[str] = mapped_column(String(16), default="parsing", nullable=False)
    chunks: Mapped[int] = mapped_column(Integer, default=0)
    error_msg: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class Dataset(Base):
    __tablename__ = "datasets"
    dataset_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    file_path: Mapped[str] = mapped_column(String(512), nullable=False)
    file_ext: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    col_count: Mapped[int] = mapped_column(Integer, default=0)
    columns_info: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class Session(Base):
    __tablename__ = "sessions"
    session_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    title: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    mode: Mapped[str] = mapped_column(String(16), default="chat", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)


class ChatMessage(Base):
    __tablename__ = "chat_messages"
    id: Mapped[int] = mapped_column(AUTO_ID_TYPE, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(32), ForeignKey("sessions.session_id", ondelete="CASCADE"), nullable=False)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class AnalysisResult(Base):
    __tablename__ = "analysis_results"
    id: Mapped[int] = mapped_column(AUTO_ID_TYPE, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(BigInteger, ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    session_id: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    code: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    stdout: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    table_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    chart_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    error_msg: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), default="running", nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(AUTO_ID_TYPE, primary_key=True, autoincrement=True)
    user_id: Mapped[Optional[int]] = mapped_column(BigInteger, nullable=True)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    target_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    ip_address: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


# ==================== 会话工具 ====================

def get_db():
    """FastAPI 依赖：每次请求一个独立 session，请求结束自动关闭"""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def get_session():
    """非请求上下文使用的会话（需手动关闭）"""
    return SessionLocal()


# ==================== 单用户模式默认用户 ====================

DEFAULT_USER_ID = 1
DEFAULT_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD")
if not DEFAULT_ADMIN_PASSWORD:
    raise RuntimeError("未设置 ADMIN_PASSWORD，请在 .env 中设置强密码")

def ensure_default_user() -> int:
    """确保默认用户存在，返回其 id（单用户模式下所有数据归属此用户）

    首次启动：用 .env 中的 ADMIN_PASSWORD 生成 bcrypt 哈希写入数据库。
    已存在但 password_hash 仍是占位 "!"（旧版本遗留）：用 bcrypt 哈希覆盖。
    已存在且 password_hash 是有效 bcrypt 哈希：不覆盖（用户可能已改过密码）。
    """
    with get_session() as db:
        user = db.query(User).filter(User.username == DEFAULT_USERNAME).first()
        if user is None:
            user = User(
                username=DEFAULT_USERNAME,
                password_hash=hash_password(DEFAULT_ADMIN_PASSWORD),
                display_name="管理员",
                role="admin",
            )
            db.add(user)
            db.commit()
            db.refresh(user)
        elif user.password_hash == "!":
            # 旧版本遗留的占位哈希，升级为真实 bcrypt 哈希
            user.password_hash = hash_password(DEFAULT_ADMIN_PASSWORD)
            db.commit()
        return user.id
