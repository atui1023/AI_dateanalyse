"""知识库（RAG）模块：文档入库、异步向量化、向量检索、知识库（文件夹）管理。

核心设计：
- “临时文件”是一个系统级知识库文件夹（ID 固定为 default），所有新上传文件
  默认进入该文件夹；用户可新建自定义知识库并把文件移动过去（仅改外键，不重新向量化）。
- 文档注册表（kb_docs.json）独立于向量库，记录每个文件的解析状态
  （parsing 解析中 / ready 已就绪 / failed 解析失败），支撑异步上传与失败重试。
- 上传后立即入队后台任务完成 解析 → 切分 → 向量化；失败时清理半成品向量，杜绝幽灵数据。
- 删除文件时同步删除 Chroma 中对应的 chunk 与 vector；删除知识库时文件退回“临时文件”，
  严禁级联删除向量。

技术栈：LangChain（RecursiveCharacterTextSplitter / OpenAIEmbeddings / Chroma），
Chroma 本地持久化到 chroma_db/。重依赖采用函数内延迟导入，不影响数据分析主流程启动。
"""
import os
import queue
import sys
import threading
import uuid
from datetime import datetime
from typing import List, Optional, Tuple

from dotenv import load_dotenv

from db import (
    DEFAULT_USER_ID, ensure_default_user, get_session,
    KbFolder, KbDocument,
)

load_dotenv()

API_KEY = os.getenv("API_KEY")
BASE_URL = os.getenv("BASE_URL")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-v3")
CHROMA_DIR = os.getenv(
    "CHROMA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "chroma_db")
)

KB_EXTENSIONS = (".txt", ".md", ".pdf", ".csv", ".xlsx", ".xls")
MAX_KB_FILE_SIZE = 20 * 1024 * 1024  # 20MB
TOP_K = 4  # 每次提问召回的资料段数
TEMP_FOLDER_ID = "default"  # 系统知识库：临时文件区（固定 ID，不可重命名/删除）
TEMP_FOLDER_NAME = "临时文件"

# 文档解析状态
STATUS_PARSING = "parsing"  # 解析中（已入队，尚未向量化）
STATUS_READY = "ready"      # 已就绪（向量可检索）
STATUS_FAILED = "failed"    # 解析失败（可重试）

_embeddings = None
_vectordb = None

# 后台向量化任务队列：单 worker 串行执行，避免 Chroma SQLite 并发写冲突
_ingest_queue: "queue.Queue[str]" = queue.Queue()
_worker_started = False
_worker_lock = threading.Lock()


# ==================== 向量库与 Embedding（懒加载） ====================

def get_embeddings():
    """Embedding 客户端（懒加载，全进程复用）"""
    global _embeddings
    if _embeddings is None:
        invalid_values = ("your_", "replace_", "your-llm-endpoint")
        if (
            not API_KEY
            or not BASE_URL
            or any(token in API_KEY.lower() for token in invalid_values)
            or any(token in BASE_URL.lower() for token in invalid_values)
        ):
            raise RuntimeError(
                "知识库向量化未配置：请在 .env 中填写真实的 API_KEY 和 BASE_URL"
            )
        from langchain_openai import OpenAIEmbeddings

        _embeddings = OpenAIEmbeddings(
            model=EMBEDDING_MODEL,
            api_key=API_KEY,
            base_url=BASE_URL,
            chunk_size=10,  # DashScope 单请求批量上限，保守取 10
            # 关闭本地 token 化，直接发送原文文本（非 OpenAI 官方端点必需）
            check_embedding_ctx_length=False,
        )
    return _embeddings


def get_vectordb():
    """Chroma 向量库（懒加载，本地持久化）"""
    global _vectordb
    if _vectordb is None:
        import chromadb
        from langchain_chroma import Chroma

        client = chromadb.PersistentClient(path=CHROMA_DIR)
        _vectordb = Chroma(
            client=client,
            collection_name="kb_store",
            embedding_function=get_embeddings(),
        )
    return _vectordb


# ==================== 文件解析 ====================

def _read_text(path: str) -> str:
    """读取文本文件，自动尝试常见编码"""
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    raise ValueError("无法识别文件编码，请将文件另存为 UTF-8 后重试")


def _tabular_to_pages(path: str, ext: str) -> List[str]:
    """表格文件（CSV/Excel）转文本页：字段说明 + 逐行记录，便于按行语义检索"""
    import pandas as pd

    if ext == ".csv":
        try:
            df = pd.read_csv(path)
        except UnicodeDecodeError:
            df = pd.read_csv(path, encoding="gbk")
    else:
        df = pd.read_excel(path)

    cols = [str(c) for c in df.columns]
    lines = [f"数据表 {os.path.basename(path)}，共 {len(df)} 行，字段：" + "、".join(cols)]
    for _, row in df.head(500).iterrows():  # 上限 500 行，防止超大表拖垮切分与 embedding
        cells = [f"{c}={row[c]}" for c in df.columns if pd.notna(row[c])]
        if cells:
            lines.append("；".join(cells))
    return ["\n".join(lines)]


def _load_pages(path: str, ext: str) -> List[str]:
    """解析文档为文本页列表"""
    if ext == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(path)
        return [(page.extract_text() or "") for page in reader.pages]
    if ext in (".csv", ".xlsx", ".xls"):
        return _tabular_to_pages(path, ext)
    return [_read_text(path)]


def _get_splitter():
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    # 中文文档：优先按段落/句子切，chunk 500 字、相邻块重叠 50 字
    return RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=50,
        separators=["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""],
    )


# ==================== 文档注册表（MySQL: kb_documents） ====================
# 数据模型：
#   Document: {doc_id, filename, path, ext, folder_id, status, chunks, error, created_at, active}
#   folder_id 软关联 Folder.id；文件移动只改此外键（向量块 metadata 同步更新），不重新向量化
#   active 控制单个文件是否参与检索（默认 True）；文件夹未勾选时整库都不参与

def _doc_to_dict(d: KbDocument) -> dict:
    """ORM -> dict（保持与旧 JSON 结构兼容，供上层无感知使用）"""
    return {
        "doc_id": d.doc_id,
        "user_id": d.user_id,
        "filename": d.filename,
        "path": d.file_path,
        "ext": d.file_ext,
        "folder_id": d.folder_id or TEMP_FOLDER_ID,
        "status": d.status,
        "chunks": d.chunks or 0,
        "error": d.error_msg or "",
        "active": bool(d.is_active),
        "created_at": d.created_at.isoformat(timespec="seconds") if d.created_at else "",
    }


def _load_docs(user_id: int = DEFAULT_USER_ID) -> List[dict]:
    """从 MySQL 读取该用户的全部文档（含自愈：归属已删除知识库的退回临时文件区）"""
    with get_session() as db:
        valid_folders = {f.id for f in db.query(KbFolder).filter(KbFolder.user_id == user_id).all()}
        sys_folder = db.query(KbFolder).filter(
            KbFolder.is_system == True, KbFolder.user_id == user_id
        ).first()
        temp_id = sys_folder.id if sys_folder else TEMP_FOLDER_ID
        rows = db.query(KbDocument).filter(KbDocument.user_id == user_id).order_by(
            KbDocument.created_at.desc()
        ).all()
        docs = []
        changed = False
        for d in rows:
            if d.folder_id not in valid_folders:
                d.folder_id = temp_id
                changed = True
            docs.append(_doc_to_dict(d))
        if changed:
            db.commit()
        return docs


def _find_doc(doc_id: str, user_id: Optional[int] = DEFAULT_USER_ID) -> Optional[dict]:
    # doc_id 是主键，加 user_id 过滤是防跨用户访问的安全校验；
    # 传 user_id=None 表示跳过过滤（后台 worker 不在用户请求上下文中使用）。
    with get_session() as db:
        q = db.query(KbDocument).filter(KbDocument.doc_id == doc_id)
        if user_id is not None:
            q = q.filter(KbDocument.user_id == user_id)
        d = q.first()
        return _doc_to_dict(d) if d else None


def _upsert_doc(doc: dict) -> None:
    """新增或更新一条文档记录（MySQL upsert）"""
    with get_session() as db:
        d = db.get(KbDocument, doc["doc_id"])
        if d is None:
            d = KbDocument(
                doc_id=doc["doc_id"],
                user_id=doc.get("user_id", DEFAULT_USER_ID),
                folder_id=doc.get("folder_id") or TEMP_FOLDER_ID,
                filename=doc["filename"],
                file_path=doc["path"],
                file_ext=doc.get("ext"),
                status=doc.get("status", STATUS_PARSING),
                chunks=int(doc.get("chunks") or 0),
                error_msg=doc.get("error") or None,
                is_active=bool(doc.get("active", True)),
            )
            db.add(d)
        else:
            d.folder_id = doc.get("folder_id") or TEMP_FOLDER_ID
            d.filename = doc.get("filename", d.filename)
            d.file_path = doc.get("path", d.file_path)
            d.file_ext = doc.get("ext", d.file_ext)
            d.status = doc.get("status", d.status)
            d.chunks = int(doc.get("chunks") or 0)
            d.error_msg = doc.get("error") or None
            d.is_active = bool(doc.get("active", True))
        db.commit()


# ==================== 上传登记 + 异步向量化 ====================

def register_document(path: str, ext: str, filename: str,
                      folder_id: Optional[str] = None,
                      user_id: int = DEFAULT_USER_ID) -> dict:
    """登记新上传的文档：状态置为“解析中”并入队后台向量化，立即返回（上传不阻塞）"""
    folders = _load_folders(user_id)
    valid_ids = [f["id"] for f in folders]
    temp_id = next((f["id"] for f in folders if f.get("system")), TEMP_FOLDER_ID)
    doc = {
        "doc_id": uuid.uuid4().hex,
        "user_id": user_id,
        "filename": filename,
        "path": path,
        "ext": ext,
        # 兜底：未指定/无效归属一律进入系统“临时文件”区
        "folder_id": folder_id if folder_id in valid_ids else temp_id,
        "status": STATUS_PARSING,
        "chunks": 0,
        "error": "",
        "active": True,  # 默认参与检索
        "created_at": datetime.now().isoformat(timespec="seconds"),
    }
    _upsert_doc(doc)
    _enqueue_ingest(doc["doc_id"])
    return doc


def _ensure_worker() -> None:
    """启动单例后台 worker（守护线程，串行消费向量化任务）"""
    global _worker_started
    with _worker_lock:
        if not _worker_started:
            threading.Thread(target=_ingest_worker, daemon=True).start()
            _worker_started = True


def _enqueue_ingest(doc_id: str) -> None:
    _ensure_worker()
    _ingest_queue.put(doc_id)


def _ingest_worker() -> None:
    while True:
        doc_id = _ingest_queue.get()
        try:
            _do_ingest(doc_id)
        except Exception as e:  # 安全网：任何意外都不让 worker 线程死掉
            print("ingest worker fatal:", type(e).__name__, e, file=sys.stderr)
        finally:
            _ingest_queue.task_done()


def _do_ingest(doc_id: str) -> None:
    """后台任务：解析 → 切分 → 向量化 → 落状态。

    成功：status=ready；失败：status=failed 并记录中文错误信息，
    同时清理可能已写入的部分向量，防止“幽灵数据”造成检索幻觉。
    """
    # worker 不在用户请求上下文中，不带 user_id 过滤；doc 字典自带 user_id，
    # _upsert_doc 更新已存在记录时不会改 user_id（保留原值），保证归属正确。
    doc = _find_doc(doc_id, user_id=None)
    if doc is None or doc["status"] == STATUS_READY:
        return
    try:
        if not os.path.exists(doc["path"]):
            raise ValueError("原始文件已丢失或被移动，请重新上传")
        pages = [p for p in _load_pages(doc["path"], doc["ext"]) if p.strip()]
        if not pages:
            raise ValueError("未能从文件中提取到文本内容（扫描版 PDF 或空文件无法识别）")

        pieces = _get_splitter().create_documents(pages)
        texts = [d.page_content.strip() for d in pieces if d.page_content.strip()]
        if not texts:
            raise ValueError("文件内容为空或无法切分")

        # 归属以执行时刻注册表中的 folder_id 为准（解析期间用户移动文件也能正确落位）
        folder_id = doc["folder_id"]
        owner_id = doc.get("user_id", DEFAULT_USER_ID)
        metadatas = [
            {
                "doc_id": doc_id,
                "filename": doc["filename"],
                "path": doc["path"],
                "chunk": i,
                "folder_id": folder_id,
                "user_id": owner_id,  # 向量级归属：retrieve 强制按此过滤，防跨用户召回
            }
            for i in range(len(texts))
        ]
        # 显式指定 chunk id（doc_id_序号），便于后续按文档精确更新/删除
        ids = [f"{doc_id}_{i}" for i in range(len(texts))]
        get_vectordb().add_texts(texts, metadatas=metadatas, ids=ids)

        doc["status"] = STATUS_READY
        doc["chunks"] = len(texts)
        doc["error"] = ""
    except Exception as e:
        try:  # 清理半成品向量
            owner_id = doc.get("user_id", DEFAULT_USER_ID)
            get_vectordb()._collection.delete(
                where={"$and": [{"doc_id": doc_id}, {"user_id": owner_id}]}
            )
        except Exception:
            pass
        doc["status"] = STATUS_FAILED
        doc["chunks"] = 0
        doc["error"] = str(e)[:200]
        print(f"ingest failed [{doc['filename']}]: {e}", file=sys.stderr)
    finally:
        _upsert_doc(doc)


def backfill_vector_owner() -> dict:
    """启动时向量库一致性维护（幂等）：
    1) 为历史向量块补写 user_id metadata（多用户隔离上线前的旧数据没有该字段）；
    2) 清理"孤儿向量"：chroma 中存在但 MySQL 注册表已无记录的 doc_id 块
       （历史删除残留，user_id=None 永远不会被 retrieve 召回，属垃圾数据）。
    返回 {"fixed": 补归属块数, "orphaned": 清理孤儿块数}。"""
    fixed = 0
    orphaned = 0
    try:
        with get_session() as db:
            rows = db.query(KbDocument).all()
            owners = {d.doc_id: d.user_id for d in rows}
        col = get_vectordb()._collection
        # 1) 补归属
        for doc_id, owner_id in owners.items():
            got = col.get(where={"doc_id": doc_id}, include=["metadatas"])
            ids = got.get("ids") or []
            metas = got.get("metadatas") or []
            update_ids, update_metas = [], []
            for cid, m in zip(ids, metas):
                m = m or {}
                if m.get("user_id") != owner_id:
                    update_ids.append(cid)
                    update_metas.append({**m, "user_id": owner_id})
            if update_ids:
                col.update(ids=update_ids, metadatas=update_metas)
                fixed += len(update_ids)
        # 2) 清孤儿：向量库里 doc_id 不在注册表中的块，整 doc 删除
        all_got = col.get(include=["metadatas"])
        for m in all_got.get("metadatas") or []:
            m = m or {}
            did = m.get("doc_id")
            if did and did not in owners:
                try:
                    col.delete(where={"doc_id": did})
                    orphaned += 1
                except Exception as e:
                    print(f"orphan vector delete failed [{did}]: {e}", file=sys.stderr)
    except Exception as e:
        print(f"backfill vector owner failed: {type(e).__name__}: {e}", file=sys.stderr)
    return {"fixed": fixed, "orphaned": orphaned}


def retry_document(doc_id: str, user_id: int = DEFAULT_USER_ID) -> dict:
    """重试解析失败的文档（重新入队向量化）"""
    doc = _find_doc(doc_id, user_id)
    if doc is None:
        raise ValueError("文档不存在或已删除")
    if doc["status"] == STATUS_PARSING:
        raise ValueError("该文档正在解析中，请稍候")
    if not os.path.exists(doc["path"]):
        raise ValueError("原始文件已丢失，请删除后重新上传")
    doc["status"] = STATUS_PARSING
    doc["error"] = ""
    _upsert_doc(doc)
    _enqueue_ingest(doc_id)
    return doc


# ==================== 文档查询与管理 ====================

def list_documents(user_id: int = DEFAULT_USER_ID) -> List[dict]:
    """列出全部文档（来自注册表，含解析状态；新的在前）"""
    docs = _load_docs(user_id)
    return sorted(docs, key=lambda d: d.get("created_at", ""), reverse=True)


def get_document(doc_id: str, user_id: int = DEFAULT_USER_ID) -> dict:
    doc = _find_doc(doc_id, user_id)
    if doc is None:
        raise ValueError("文档不存在或已删除")
    return doc


def delete_document(doc_id: str, user_id: int = DEFAULT_USER_ID) -> Optional[dict]:
    """删除文档：同步删除向量库中该文档的全部 chunk/vector（防幽灵数据）+ 注册表记录。
    返回被删记录（供调用方清理磁盘文件与内存数据集）。"""
    doc = _find_doc(doc_id, user_id)
    try:
        get_vectordb()._collection.delete(where={"$and": [{"doc_id": doc_id}, {"user_id": user_id}]})
    except Exception as e:
        print("kb delete vectors failed:", e, file=sys.stderr)
    if doc is not None:
        with get_session() as db:
            d = db.query(KbDocument).filter(
                KbDocument.doc_id == doc_id, KbDocument.user_id == user_id
            ).first()
            if d:
                db.delete(d)
                db.commit()
    return doc


def move_document(doc_id: str, folder_id: str, user_id: int = DEFAULT_USER_ID) -> dict:
    """移动文档到指定知识库：仅更新外键（注册表 + 已写入向量块的 metadata），不重新向量化"""
    if folder_id not in [f["id"] for f in _load_folders(user_id)]:
        raise ValueError("目标知识库不存在")
    doc = _find_doc(doc_id, user_id)
    if doc is None:
        raise ValueError("文档不存在或已删除")
    doc["folder_id"] = folder_id
    _upsert_doc(doc)
    # 已就绪的向量块同步 metadata；解析中的块会在入库时读取最新归属，无需处理
    if doc["status"] == STATUS_READY:
        try:
            col = get_vectordb()._collection
            got = col.get(where={"doc_id": doc_id}, include=["metadatas"])
            ids = got.get("ids") or []
            if ids:
                metas = [{**(m or {}), "folder_id": folder_id} for m in got.get("metadatas") or []]
                col.update(ids=ids, metadatas=metas)
        except Exception as e:
            print("kb move metadata update failed:", e, file=sys.stderr)
    return doc


# ==================== 检索 ====================

def retrieve(query: str, k: int = TOP_K,
             folder_ids: Optional[List[str]] = None,
             doc_ids: Optional[List[str]] = None,
             user_id: int = DEFAULT_USER_ID):
    """按语义相似度召回 top-k 资料块。

    多用户隔离（强制）：向量库是所有用户共享的 collection，每个 chunk 的 metadata
    都带 user_id，这里始终追加 {"user_id": user_id} 过滤条件——即使用户未勾选任何
    知识库（folder_ids/doc_ids 均为空），也只在本人文档范围内检索，绝不跨用户召回。

    folder_ids 不为空时只在这些知识库内检索（按需加载：勾选即时生效）；
    doc_ids 不为空时进一步只检索这些文档（文件级勾选，与 folder_ids 取交集）。
    解析中/失败的文档没有向量块，天然不会被召回。
    """
    # 归属条件始终第一优先，任何检索都不能越过
    conds = [{"user_id": {"$eq": user_id}}]
    if folder_ids:
        conds.append({"folder_id": {"$in": folder_ids}})
    if doc_ids:
        conds.append({"doc_id": {"$in": doc_ids}})
    filter_cond = conds[0] if len(conds) == 1 else {"$and": conds}

    return get_vectordb().similarity_search(query, k=k, filter=filter_cond)


def doc_count(user_id: int = DEFAULT_USER_ID) -> int:
    """该用户已就绪文档数（用于判断库是否为空，避免无谓的检索/embedding 调用）"""
    try:
        with get_session() as db:
            return db.query(KbDocument).filter(
                KbDocument.user_id == user_id,
                KbDocument.status == STATUS_READY,
            ).count()
    except Exception:
        return 0


def build_context(docs) -> Tuple[str, List[dict]]:
    """把召回的资料拼成 prompt 上下文，并整理出处列表"""
    parts, sources = [], []
    for i, d in enumerate(docs, 1):
        fname = d.metadata.get("filename", "未知文档")
        parts.append(f"[资料{i}]（来自 {fname}）\n{d.page_content}")
        sources.append({"filename": fname, "snippet": d.page_content.strip()[:160]})
    return "\n\n".join(parts), sources


def active_folder_ids(user_id: int = DEFAULT_USER_ID) -> Optional[List[str]]:
    """当前激活（勾选）的知识库 id 列表；全部未勾选时返回 None（回退全库检索）"""
    ids = [x["id"] for x in _load_folders(user_id) if x.get("active")]
    return ids or None


def active_doc_ids(user_id: int = DEFAULT_USER_ID) -> Optional[List[str]]:
    """当前激活（勾选）的文档 id 列表；全部未勾选时返回 None（不额外过滤）。
    与文件夹勾选叠加：文件夹未勾选则整库不检索，文件级勾选在文件夹基础上进一步收窄。
    """
    ids = [d["doc_id"] for d in _load_docs(user_id) if d.get("active", True)]
    return ids or None


def set_doc_active(doc_id: str, active: bool, user_id: int = DEFAULT_USER_ID) -> dict:
    """切换单个文档的激活状态（是否参与检索）"""
    doc = _find_doc(doc_id, user_id)
    if doc is None:
        raise ValueError("文档不存在或已删除")
    doc["active"] = bool(active)
    _upsert_doc(doc)
    return {"doc_id": doc_id, "active": bool(active)}


# ==================== 知识库（文件夹）管理 ====================
# 数据模型（MySQL 表 kb_folders）：
#   Folder: {id, name, active, system, created_at}
#   关系：Document.folder_id → Folder.id；知识库被删时其下文档退回系统“临时文件”区

def _folder_to_dict(f: KbFolder) -> dict:
    return {
        "id": f.id,
        "name": f.name,
        "active": bool(f.is_active),
        "system": bool(f.is_system),
        "created_at": f.created_at.isoformat(timespec="seconds") if f.created_at else "",
    }


def _ensure_system_folder(user_id: int = DEFAULT_USER_ID) -> str:
    """确保该用户的系统“临时文件”知识库存在（自愈：误删/旧版名称时自动修复）。

    每个用户各自拥有一个系统临时库：默认用户复用固定 id（TEMP_FOLDER_ID，
    兼容旧数据），其它用户使用 "{TEMP_FOLDER_ID}_{user_id}" 作为 id。返回该库 id。
    """
    fid = TEMP_FOLDER_ID if user_id == DEFAULT_USER_ID else f"{TEMP_FOLDER_ID}_{user_id}"
    with get_session() as db:
        f = db.query(KbFolder).filter(
            KbFolder.is_system == True, KbFolder.user_id == user_id
        ).first()
        if f is None:
            f = KbFolder(
                id=fid,
                user_id=user_id,
                name=TEMP_FOLDER_NAME,
                is_system=True,
                is_active=True,
            )
            db.add(f)
        else:
            f.is_system = True
            if f.name != TEMP_FOLDER_NAME:
                f.name = TEMP_FOLDER_NAME
        db.commit()
        return f.id


def _load_folders(user_id: int = DEFAULT_USER_ID) -> List[dict]:
    """从 MySQL 读取该用户的知识库列表（系统库排最前）"""
    _ensure_system_folder(user_id)
    with get_session() as db:
        rows = db.query(KbFolder).filter(KbFolder.user_id == user_id).order_by(
            KbFolder.is_system.desc(), KbFolder.created_at.asc()
        ).all()
        return [_folder_to_dict(f) for f in rows]


def list_folders(user_id: int = DEFAULT_USER_ID) -> List[dict]:
    """知识库列表（含每个库的文件数与向量段数，供前端展示）"""
    folders = _load_folders(user_id)
    doc_n: dict = {}
    chunk_n: dict = {}
    for d in _load_docs(user_id):
        fid = d.get("folder_id") or TEMP_FOLDER_ID
        doc_n[fid] = doc_n.get(fid, 0) + 1
        chunk_n[fid] = chunk_n.get(fid, 0) + int(d.get("chunks") or 0)
    return [
        {
            "id": x["id"],
            "name": x["name"],
            "active": bool(x.get("active", True)),
            "system": bool(x.get("system")),
            "docs": doc_n.get(x["id"], 0),
            "chunks": chunk_n.get(x["id"], 0),
        }
        for x in folders
    ]


def create_folder(name: str, user_id: int = DEFAULT_USER_ID) -> dict:
    """新建知识库（同用户下重名校验）"""
    name = (name or "").strip()
    if not name:
        raise ValueError("知识库名称不能为空")
    with get_session() as db:
        if db.query(KbFolder).filter(
            KbFolder.name == name, KbFolder.user_id == user_id
        ).first():
            raise ValueError(f"已存在同名知识库：{name}")
        fid = uuid.uuid4().hex
        db.add(KbFolder(
            id=fid,
            user_id=user_id,
            name=name,
            is_system=False,
            is_active=True,
        ))
        db.commit()
    return {"id": fid, "name": name, "active": True, "system": False,
            "created_at": datetime.now().isoformat(timespec="seconds")}


def rename_folder(folder_id: str, name: str, user_id: int = DEFAULT_USER_ID) -> dict:
    """重命名知识库（系统“临时文件”库不可重命名）"""
    name = (name or "").strip()
    if not name:
        raise ValueError("知识库名称不能为空")
    with get_session() as db:
        f = db.get(KbFolder, folder_id)
        if f is None or f.user_id != user_id:
            raise ValueError("知识库不存在")
        if f.is_system:
            raise ValueError("系统临时文件库不可重命名")
        dup = db.query(KbFolder).filter(
            KbFolder.name == name, KbFolder.id != folder_id, KbFolder.user_id == user_id
        ).first()
        if dup:
            raise ValueError(f"已存在同名知识库：{name}")
        f.name = name
        db.commit()
        return _folder_to_dict(f)


def set_folder_active(folder_id: str, active: bool, user_id: int = DEFAULT_USER_ID) -> dict:
    """切换知识库激活状态（勾选=参与检索；状态持久化，重启不丢）"""
    with get_session() as db:
        f = db.get(KbFolder, folder_id)
        if f is None or f.user_id != user_id:
            raise ValueError("知识库不存在")
        f.is_active = bool(active)
        db.commit()
        return _folder_to_dict(f)


def delete_folder(folder_id: str, user_id: int = DEFAULT_USER_ID) -> int:
    """删除知识库：其下所有文件退回系统“临时文件”库（只改外键 + 向量 metadata，
    数据安全优先，严禁级联删除文件与向量）。返回移动的文件数。"""
    if folder_id == TEMP_FOLDER_ID:
        raise ValueError("系统临时文件库不可删除")
    with get_session() as db:
        f = db.get(KbFolder, folder_id)
        if f is None or f.user_id != user_id:
            raise ValueError("知识库不存在")
        if f.is_system:
            raise ValueError("系统临时文件库不可删除")
        # 目标临时文件区（该用户的系统库 id，缺失时兜底常量）
        sys_folder = db.query(KbFolder).filter(
            KbFolder.is_system == True, KbFolder.user_id == user_id
        ).first()
        temp_id = sys_folder.id if sys_folder else TEMP_FOLDER_ID
        # 只移动该用户、该库下的文档外键回临时文件区
        moved = db.query(KbDocument).filter(
            KbDocument.folder_id == folder_id, KbDocument.user_id == user_id
        ).update({KbDocument.folder_id: temp_id})
        db.delete(f)
        db.commit()
    # 向量块 metadata 同步改归属
    try:
        _move_folder_chunks(folder_id, temp_id)
    except Exception as e:
        print("kb delete folder metadata update failed:", e, file=sys.stderr)
    return moved


def _move_folder_chunks(from_folder_id: str, to_folder_id: str) -> int:
    """把某知识库下所有向量块改归属到目标知识库（Chroma metadata update）"""
    col = get_vectordb()._collection
    got = col.get(where={"folder_id": from_folder_id}, include=["metadatas"])
    ids = got.get("ids") or []
    if not ids:
        return 0
    metas = got.get("metadatas") or []
    new_metas = [{**(m or {}), "folder_id": to_folder_id} for m in metas]
    col.update(ids=ids, metadatas=new_metas)
    return len(ids)
