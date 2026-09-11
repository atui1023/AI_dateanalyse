"""Migrate the local SQLite database to MySQL with backups and count checks."""

from __future__ import annotations

import argparse
import re
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from urllib.parse import quote_plus

from dotenv import dotenv_values
from sqlalchemy import MetaData, create_engine, inspect, text


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from db import Base  # noqa: E402


TABLE_ORDER = [
    "users",
    "kb_folders",
    "kb_documents",
    "kb_document_versions",
    "datasets",
    "sessions",
    "chat_messages",
    "analysis_results",
    "analysis_relations",
    "dashboards",
    "dashboard_items",
    "analysis_shares",
    "analysis_comments",
    "schedule_jobs",
    "schedule_runs",
    "audit_logs",
]


def mysql_urls(config: dict[str, str | None]) -> tuple[str, str, str]:
    required = ["DB_HOST", "DB_NAME", "DB_USER", "DB_PASSWORD"]
    missing = [key for key in required if not config.get(key)]
    if missing:
        raise RuntimeError(f"Missing MySQL settings: {', '.join(missing)}")
    database = str(config["DB_NAME"])
    if not re.fullmatch(r"[A-Za-z0-9_]+", database):
        raise RuntimeError("DB_NAME may contain only letters, digits and underscores")
    host = str(config["DB_HOST"])
    port = int(config.get("DB_PORT") or 3306)
    user = quote_plus(str(config["DB_USER"]))
    password = quote_plus(str(config["DB_PASSWORD"]))
    server = f"mysql+pymysql://{user}:{password}@{host}:{port}/?charset=utf8mb4"
    target = f"mysql+pymysql://{user}:{password}@{host}:{port}/{database}?charset=utf8mb4"
    return server, target, database


def backup_sqlite(source: Path, backup_dir: Path, stamp: str) -> Path:
    backup_dir.mkdir(parents=True, exist_ok=True)
    destination = backup_dir / f"data_analysis_{stamp}.db"
    with sqlite3.connect(source) as source_db, sqlite3.connect(destination) as backup_db:
        source_db.backup(backup_db)
    return destination


def backup_mysql(server_engine, database: str, stamp: str) -> str | None:
    backup_name = f"{database}_backup_{stamp}"
    with server_engine.begin() as conn:
        tables = [row[0] for row in conn.execute(text(
            "SELECT TABLE_NAME FROM INFORMATION_SCHEMA.TABLES "
            "WHERE TABLE_SCHEMA=:database AND TABLE_TYPE='BASE TABLE'"
        ), {"database": database})]
        if not tables:
            return None
        conn.execute(text(f"CREATE DATABASE `{backup_name}` CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for table in tables:
            conn.execute(text(f"CREATE TABLE `{backup_name}`.`{table}` LIKE `{database}`.`{table}`"))
            conn.execute(text(f"INSERT INTO `{backup_name}`.`{table}` SELECT * FROM `{database}`.`{table}`"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    return backup_name


def recreate_target(server_engine, target_engine, database: str) -> None:
    with server_engine.begin() as conn:
        conn.execute(text(
            f"CREATE DATABASE IF NOT EXISTS `{database}` "
            "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
        ))
    with target_engine.begin() as conn:
        conn.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for table in inspect(target_engine).get_table_names():
            conn.execute(text(f"DROP TABLE IF EXISTS `{table}`"))
        conn.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    Base.metadata.create_all(target_engine)


def migrate_rows(sqlite_url: str, target_engine) -> dict[str, int]:
    source_engine = create_engine(sqlite_url)
    source_meta = MetaData()
    source_meta.reflect(source_engine)
    target_meta = MetaData()
    target_meta.reflect(target_engine)
    source_tables = set(inspect(source_engine).get_table_names())
    counts: dict[str, int] = {}

    with source_engine.connect() as source, target_engine.begin() as target:
        target.execute(text("SET FOREIGN_KEY_CHECKS=0"))
        for table_name in TABLE_ORDER:
            if table_name not in source_tables or table_name not in target_meta.tables:
                continue
            rows = [dict(row._mapping) for row in source.execute(source_meta.tables[table_name].select())]
            if rows:
                target.execute(target_meta.tables[table_name].insert(), rows)
            counts[table_name] = len(rows)
        target.execute(text("SET FOREIGN_KEY_CHECKS=1"))
    source_engine.dispose()
    return counts


def verify_counts(target_engine, expected: dict[str, int]) -> None:
    mismatches = []
    with target_engine.connect() as conn:
        for table, source_count in expected.items():
            target_count = int(conn.execute(text(f"SELECT COUNT(*) FROM `{table}`")).scalar() or 0)
            print(f"{table}: SQLite={source_count}, MySQL={target_count}")
            if source_count != target_count:
                mismatches.append(f"{table}: {source_count} != {target_count}")
    if mismatches:
        raise RuntimeError("Count verification failed: " + "; ".join(mismatches))


def switch_env(env_path: Path, target_url: str) -> None:
    lines = env_path.read_text(encoding="utf-8").splitlines()
    replacement = f"DATABASE_URL={target_url}"
    replaced = False
    result = []
    for line in lines:
        if line.startswith("DATABASE_URL="):
            result.append(replacement)
            replaced = True
        else:
            result.append(line)
    if not replaced:
        result.append(replacement)
    env_path.write_text("\n".join(result) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--switch-env", action="store_true")
    args = parser.parse_args()

    env_path = ROOT / ".env"
    sqlite_path = ROOT / "data_analysis.db"
    if not sqlite_path.exists():
        raise RuntimeError(f"SQLite database not found: {sqlite_path}")
    config = dotenv_values(env_path)
    server_url, target_url, database = mysql_urls(config)
    server_engine = create_engine(server_url, pool_pre_ping=True)
    target_engine = create_engine(target_url, pool_pre_ping=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    sqlite_backup = backup_sqlite(sqlite_path, ROOT / "backups", stamp)
    print(f"SQLite backup: {sqlite_backup}")
    mysql_backup = backup_mysql(server_engine, database, stamp)
    print(f"MySQL backup database: {mysql_backup or 'target was empty'}")
    recreate_target(server_engine, target_engine, database)
    expected = migrate_rows(f"sqlite:///{sqlite_path.as_posix()}", target_engine)
    verify_counts(target_engine, expected)
    if args.switch_env:
        switch_env(env_path, target_url)
        print("DATABASE_URL switched to MySQL")
    target_engine.dispose()
    server_engine.dispose()
    print("Migration completed successfully")


if __name__ == "__main__":
    main()
