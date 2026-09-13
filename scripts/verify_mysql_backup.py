"""Verify that a MySQL backup database has the same table counts as its source."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from dotenv import dotenv_values
from sqlalchemy import create_engine, inspect, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from migrate_sqlite_to_mysql import mysql_urls  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup_database", help="数据库备份名，例如 aidataanalysis_backup_20260913_120000")
    args = parser.parse_args()
    config = dotenv_values(ROOT / ".env")
    server_url, target_url, source_database = mysql_urls(config)
    source_engine = create_engine(target_url, pool_pre_ping=True)
    backup_engine = create_engine(target_url.rsplit("/", 1)[0] + f"/{args.backup_database}?charset=utf8mb4", pool_pre_ping=True)
    try:
        source_tables = set(inspect(source_engine).get_table_names())
        backup_tables = set(inspect(backup_engine).get_table_names())
        if not backup_tables:
            raise RuntimeError(f"备份数据库不存在或为空: {args.backup_database}")
        mismatches = []
        with source_engine.connect() as source_conn, backup_engine.connect() as backup_conn:
            for table in sorted(source_tables | backup_tables):
                source_count = int(source_conn.execute(text(f"SELECT COUNT(*) FROM `{table}`")).scalar() or 0) if table in source_tables else 0
                backup_count = int(backup_conn.execute(text(f"SELECT COUNT(*) FROM `{table}`")).scalar() or 0) if table in backup_tables else 0
                print(f"{table}: source={source_count}, backup={backup_count}")
                if source_count != backup_count:
                    mismatches.append(table)
        if mismatches:
            raise RuntimeError("备份校验失败: " + ", ".join(mismatches))
        print(f"Backup verification passed for {source_database} -> {args.backup_database}")
    finally:
        backup_engine.dispose()
        source_engine.dispose()


if __name__ == "__main__":
    main()
