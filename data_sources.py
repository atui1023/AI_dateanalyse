"""外部数据源连接模块。

连接测试统一从这里进入，后续新增 PostgreSQL、SQL Server 或云端数据源时，
只需扩展连接器而不必把驱动细节散落到接口和前端。
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
import csv
import io
import json

import pandas as pd
from sqlalchemy import create_engine, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import inspect


SOURCE_TYPES = [
    {"type": "mysql", "label": "MySQL", "status": "available", "driver": "PyMySQL"},
    {"type": "postgresql", "label": "PostgreSQL", "status": "module", "driver": "psycopg2-binary"},
    {"type": "sqlserver", "label": "SQL Server", "status": "module", "driver": "pyodbc + ODBC Driver 18"},
    {"type": "api", "label": "HTTP API", "status": "available", "driver": "urllib"},
    {"type": "webhook", "label": "Webhook 数据源", "status": "available", "driver": "urllib"},
]


def list_source_types() -> list[dict[str, str]]:
    return SOURCE_TYPES.copy()


def _required(config: dict[str, Any], key: str) -> str:
    value = str(config.get(key) or "").strip()
    if not value:
        raise ValueError(f"缺少连接参数：{key}")
    return value


def _connection_url(config: dict[str, Any]) -> tuple[URL | str, str]:
    source_type = str(config.get("type") or "mysql").lower()
    host = _required(config, "host")
    database = _required(config, "database")
    username = _required(config, "username")
    password = str(config.get("password") or "")
    port = int(config.get("port") or {"mysql": 3306, "postgresql": 5432, "sqlserver": 1433}.get(source_type, 0))
    if not 1 <= port <= 65535:
        raise ValueError("端口必须在 1 到 65535 之间")
    if source_type == "mysql":
        return URL.create("mysql+pymysql", username=username, password=password, host=host, port=port, database=database, query={"charset": "utf8mb4"}), source_type
    if source_type == "postgresql":
        return URL.create("postgresql+psycopg2", username=username, password=password, host=host, port=port, database=database), source_type
    if source_type == "sqlserver":
        driver = str(config.get("odbc_driver") or "ODBC Driver 18 for SQL Server")
        return f"mssql+pyodbc://{quote_plus(username)}:{quote_plus(password)}@{host}:{port}/{quote_plus(database)}?driver={quote_plus(driver)}&TrustServerCertificate=yes", source_type
    raise ValueError("暂不支持该数据源类型")


def test_connection(config: dict[str, Any]) -> dict[str, Any]:
    url, source_type = _connection_url(config)
    try:
        engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 8} if source_type in {"mysql", "postgresql"} else {})
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
            tables = inspect(connection).get_table_names()
        engine.dispose()
        return {"type": source_type, "status": "ok", "host": str(config.get("host")), "database": str(config.get("database")), "table_count": len(tables), "tables": tables[:50]}
    except ModuleNotFoundError as exc:
        raise RuntimeError(f"缺少 {source_type} 驱动，请安装：{exc.name}") from exc
    except SQLAlchemyError as exc:
        raise RuntimeError(f"数据库连接失败：{str(exc).splitlines()[0][:300]}") from exc


def _database_engine(config: dict[str, Any]):
    url, source_type = _connection_url(config)
    try:
        return create_engine(
            url,
            pool_pre_ping=True,
            connect_args={"connect_timeout": 8} if source_type in {"mysql", "postgresql"} else {},
        ), source_type
    except ModuleNotFoundError as exc:
        raise RuntimeError(f"缺少 {source_type} 驱动，请安装：{exc.name}") from exc


def _table_query(engine, source_type: str, table_name: str, limit: int):
    quoted = engine.dialect.identifier_preparer.quote(table_name)
    if source_type == "sqlserver":
        return text(f"SELECT TOP {limit} * FROM {quoted}")
    return text(f"SELECT * FROM {quoted} LIMIT {limit}")


def inspect_table(config: dict[str, Any], table_name: str, preview_rows: int = 20) -> dict[str, Any]:
    table_name = str(table_name or "").strip()
    if not table_name:
        raise ValueError("请选择数据表")
    preview_rows = max(1, min(int(preview_rows or 20), 100))
    engine, source_type = _database_engine(config)
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            tables = inspector.get_table_names()
            if table_name not in tables:
                raise ValueError("数据表不存在，或当前账号无权访问")
            columns = [item["name"] for item in inspector.get_columns(table_name)]
            frame = pd.read_sql_query(_table_query(engine, source_type, table_name, preview_rows), connection)
            rows = frame.where(pd.notna(frame), None).to_dict(orient="records")
        return {"type": source_type, "table": table_name, "columns": columns, "rows": rows, "row_count_preview": len(rows)}
    except ValueError:
        raise
    except (SQLAlchemyError, pd.errors.DatabaseError) as exc:
        raise RuntimeError(f"读取数据表失败：{str(exc).splitlines()[0][:300]}") from exc
    finally:
        engine.dispose()


def export_table(config: dict[str, Any], table_name: str, max_rows: int = 100000) -> tuple[str, bytes, dict[str, Any]]:
    table_name = str(table_name or "").strip()
    if not table_name:
        raise ValueError("请选择要导入的数据表")
    max_rows = max(1, min(int(max_rows or 100000), 500000))
    engine, source_type = _database_engine(config)
    try:
        with engine.connect() as connection:
            inspector = inspect(connection)
            if table_name not in inspector.get_table_names():
                raise ValueError("数据表不存在，或当前账号无权访问")
            frame = pd.read_sql_query(_table_query(engine, source_type, table_name, max_rows), connection)
        output = io.StringIO(newline="")
        frame.to_csv(output, index=False)
        metadata = {"type": source_type, "table": table_name, "rows": len(frame), "columns": len(frame.columns)}
        return f"{table_name}.csv", output.getvalue().encode("utf-8-sig"), metadata
    except ValueError:
        raise
    except (SQLAlchemyError, pd.errors.DatabaseError) as exc:
        raise RuntimeError(f"导入数据表失败：{str(exc).splitlines()[0][:300]}") from exc
    finally:
        engine.dispose()


def fetch_remote_dataset(config: dict[str, Any], max_bytes: int = 20 * 1024 * 1024) -> tuple[str, bytes, str]:
    """拉取 CSV/JSON API，返回文件名、标准化后的 UTF-8 CSV 内容和格式。"""
    url = str(config.get("url") or "").strip()
    if not (url.startswith("https://") or url.startswith("http://")):
        raise ValueError("数据源 URL 只支持 http:// 或 https://")
    request = Request(url, headers={"User-Agent": "AI-Data-Analysis/1.0", "Accept": "text/csv,application/json,*/*"})
    try:
        with urlopen(request, timeout=20) as response:
            payload = response.read(max_bytes + 1)
            content_type = response.headers.get("Content-Type", "").lower()
    except Exception as exc:
        raise RuntimeError(f"远程数据源访问失败：{str(exc)[:300]}") from exc
    if len(payload) > max_bytes:
        raise ValueError(f"远程数据源超过 {max_bytes // 1024 // 1024} MB 限制")
    name = str(config.get("name") or "远程数据源")[:128]
    looks_json = "json" in content_type or url.lower().split("?", 1)[0].endswith(".json")
    if looks_json:
        try:
            parsed = json.loads(payload.decode("utf-8-sig"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError("远程 JSON 数据格式无效") from exc
        if isinstance(parsed, dict):
            parsed = parsed.get("data", parsed.get("items", parsed.get("results", parsed)))
        if not isinstance(parsed, list) or (parsed and not all(isinstance(item, dict) for item in parsed)):
            raise ValueError("JSON 数据必须是对象数组，或包含 data/items/results 对象数组")
        rows = parsed or []
        columns = list(dict.fromkeys(key for row in rows for key in row.keys()))
        output = io.StringIO(newline="")
        writer = csv.DictWriter(output, fieldnames=columns or ["value"], extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows or [{"value": ""}])
        return f"{name}.csv", output.getvalue().encode("utf-8-sig"), "api"
    try:
        text = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ValueError("远程 CSV 必须使用 UTF-8 编码") from exc
    if not text.strip():
        raise ValueError("远程 CSV 为空")
    return f"{name}.csv", text.encode("utf-8-sig"), "api"
