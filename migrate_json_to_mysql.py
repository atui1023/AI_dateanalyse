"""一次性迁移脚本：把 kb_folders.json / kb_docs.json 的数据导入 MySQL。

运行方式：python migrate_json_to_mysql.py
安全：幂等，重复运行不会产生重复数据（按主键去重）。
"""
import json
import os
import sys

from db import (
    DEFAULT_USER_ID, ensure_default_user, get_session,
    KbFolder, KbDocument,
)

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FOLDERS_FILE = os.path.join(BASE_DIR, "kb_folders.json")
DOCS_FILE = os.path.join(BASE_DIR, "kb_docs.json")


def main():
    user_id = ensure_default_user()
    assert user_id == DEFAULT_USER_ID, f"默认用户 id 应为 {DEFAULT_USER_ID}，实际 {user_id}"

    migrated_folders = 0
    migrated_docs = 0

    with get_session() as db:
        # ---------- 迁移知识库文件夹 ----------
        if os.path.exists(FOLDERS_FILE):
            with open(FOLDERS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            folders = data.get("folders") or []
            for fd in folders:
                existing = db.get(KbFolder, fd["id"])
                if existing:
                    # 已存在则更新关键字段
                    existing.name = fd.get("name", existing.name)
                    existing.is_active = bool(fd.get("active", True))
                    existing.is_system = bool(fd.get("system", False))
                else:
                    db.add(KbFolder(
                        id=fd["id"],
                        user_id=user_id,
                        name=fd["name"],
                        is_system=bool(fd.get("system", False)),
                        is_active=bool(fd.get("active", True)),
                    ))
                    migrated_folders += 1
            db.commit()
            print(f"[folders] 处理 {len(folders)} 条，新增 {migrated_folders} 条")
        else:
            print("[folders] kb_folders.json 不存在，跳过")

        # ---------- 迁移文档 ----------
        if os.path.exists(DOCS_FILE):
            with open(DOCS_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            docs = data.get("docs") or []
            # 合法 folder_id 集合（用于兜底归属）
            valid_folder_ids = {f.id for f in db.query(KbFolder).all()}
            for d in docs:
                folder_id = d.get("folder_id")
                if folder_id not in valid_folder_ids:
                    folder_id = "default"
                existing = db.get(KbDocument, d["doc_id"])
                if existing:
                    existing.folder_id = folder_id
                    existing.status = d.get("status", existing.status)
                    existing.chunks = int(d.get("chunks") or 0)
                    existing.error_msg = d.get("error") or None
                    existing.is_active = bool(d.get("active", True))
                else:
                    db.add(KbDocument(
                        doc_id=d["doc_id"],
                        user_id=user_id,
                        folder_id=folder_id,
                        filename=d["filename"],
                        file_path=d["path"],
                        file_ext=d.get("ext"),
                        file_size=int(d.get("size") or 0),
                        status=d.get("status", "ready"),
                        chunks=int(d.get("chunks") or 0),
                        error_msg=d.get("error") or None,
                        is_active=bool(d.get("active", True)),
                    ))
                    migrated_docs += 1
            db.commit()
            print(f"[docs] 处理 {len(docs)} 条，新增 {migrated_docs} 条")
        else:
            print("[docs] kb_docs.json 不存在，跳过")

    print(f"\n迁移完成：文件夹 +{migrated_folders}，文档 +{migrated_docs}")


if __name__ == "__main__":
    main()
