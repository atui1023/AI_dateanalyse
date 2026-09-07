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
import json
import os
import queue
import sys
import threading
import uuid
from datetime import datetime
from typing import List, Optional, Tuple

from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("API_KEY")
BASE_URL = os.getenv("BASE_URL")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-v3")
CHROMA_DIR = os.getenv(
    "CHROMA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "chroma_db")
)
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
FOLDERS_FILE = os.path.join(_BASE_DIR, "kb_folders.json")  # 知识库（文件夹）元数据，含激活状态
DOCS_FILE = os.path.join(_BASE_DIR, "kb_docs.json")        # 文档注册表，含解析状态

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
_folders_lock = threading.Lock()  # 知识库 JSON 读写锁
_docs_lock = threading.Lock()     # 文档注册表 JSON 读写锁

# 后台向量化任务队列：单 worker 串行执行，避免 Chroma SQLite 并发写冲突
_ingest_queue: "queue.Queue[str]" = queue.Queue()
_worker_started = False
_worker_lock = threading.Lock()


# ==================== 向量库与 Embedding（懒加载） ====================

def get_embeddings():
    """Embedding 客户端（懒加载，全进程复用）"""
    global _embeddings
    if _embeddings is None:
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


# ==================== 文档注册表（kb_docs.json） ====================
# 数据模型：
#   Document: {doc_id, filename, path, ext, folder_id, status, chunks, error, created_at, active}
#   folder_id 软关联 Folder.id；文件移动只改此外键（向量块 metadata 同步更新），不重新向量化
#   active 控制单个文件是否参与检索（默认 True）；文件夹未勾选时整库都不参与

def _backfill_docs_from_chroma() -> List[dict]:
    """首次启动且注册表不存在时，从 Chroma 已有向量回填注册表（兼容旧版本数据）"""
    try:
        data = get_vectordb().get(include=["metadatas"])
    except Exception as e:
        print("kb backfill skipped:", e, file=sys.stderr)
        return []
    by_doc: dict = {}
    for meta in data.get("metadatas") or []:
        if not meta:
            continue
        did = meta.get("doc_id")
        if not did:
            continue
        if did not in by_doc:
            by_doc[did] = {
                "doc_id": did,
                "filename": meta.get("filename", "未命名文档"),
                "path": meta.get("path", ""),
                "ext": os.path.splitext(meta.get("filename", ""))[1].lower(),
                "folder_id": meta.get("folder_id") or TEMP_FOLDER_ID,
                "status": STATUS_READY,
                "chunks": 0,
                "error": "",
                "active": True,
                "created_at": datetime.now().isoformat(timespec="seconds"),
            }
        by_doc[did]["chunks"] += 1
    return list(by_doc.values())


def _load_docs() -> List[dict]:
    """读取文档注册表；不存在时尝试从向量库回填"""
    if not os.path.exists(DOCS_FILE):
        docs = _backfill_docs_from_chroma()
        _save_docs(docs)
        return docs
    with _docs_lock:
        with open(DOCS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    docs = data.get("docs") or []
    # 自愈：归属了已删除知识库的文档，退回临时文件区
    valid_folders = {x["id"] for x in _load_folders()}
    changed = False
    for d in docs:
        if d.get("folder_id") not in valid_folders:
            d["folder_id"] = TEMP_FOLDER_ID
            changed = True
        # 兼容旧数据：补充 active 字段（默认 True）
        if "active" not in d:
            d["active"] = True
            changed = True
    if changed:
        _save_docs(docs)
    return docs


def _save_docs(docs: List[dict]) -> None:
    with _docs_lock:
        with open(DOCS_FILE, "w", encoding="utf-8") as f:
            json.dump({"docs": docs}, f, ensure_ascii=False, indent=2)


def _find_doc(doc_id: str) -> Optional[dict]:
    for d in _load_docs():
        if d["doc_id"] == doc_id:
            return d
    return None


def _upsert_doc(doc: dict) -> None:
    """新增或更新一条文档记录"""
    docs = _load_docs()
    for i, d in enumerate(docs):
        if d["doc_id"] == doc["doc_id"]:
            docs[i] = doc
            break
    else:
        docs.append(doc)
    _save_docs(docs)


# ==================== 上传登记 + 异步向量化 ====================

def register_document(path: str, ext: str, filename: str,
                      folder_id: Optional[str] = None) -> dict:
    """登记新上传的文档：状态置为“解析中”并入队后台向量化，立即返回（上传不阻塞）"""
    valid_ids = [f["id"] for f in _load_folders()]
    doc = {
        "doc_id": uuid.uuid4().hex,
        "filename": filename,
        "path": path,
        "ext": ext,
        # 兜底：未指定/无效归属一律进入系统“临时文件”区
        "folder_id": folder_id if folder_id in valid_ids else TEMP_FOLDER_ID,
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
    doc = _find_doc(doc_id)
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
        metadatas = [
            {
                "doc_id": doc_id,
                "filename": doc["filename"],
                "path": doc["path"],
                "chunk": i,
                "folder_id": folder_id,
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
            get_vectordb()._collection.delete(where={"doc_id": doc_id})
        except Exception:
            pass
        doc["status"] = STATUS_FAILED
        doc["chunks"] = 0
        doc["error"] = str(e)[:200]
        print(f"ingest failed [{doc['filename']}]: {e}", file=sys.stderr)
    finally:
        _upsert_doc(doc)


def retry_document(doc_id: str) -> dict:
    """重试解析失败的文档（重新入队向量化）"""
    doc = _find_doc(doc_id)
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

def list_documents() -> List[dict]:
    """列出全部文档（来自注册表，含解析状态；新的在前）"""
    docs = _load_docs()
    return sorted(docs, key=lambda d: d.get("created_at", ""), reverse=True)


def get_document(doc_id: str) -> dict:
    doc = _find_doc(doc_id)
    if doc is None:
        raise ValueError("文档不存在或已删除")
    return doc


def delete_document(doc_id: str) -> Optional[dict]:
    """删除文档：同步删除向量库中该文档的全部 chunk/vector（防幽灵数据）+ 注册表记录。
    返回被删记录（供调用方清理磁盘文件与内存数据集）。"""
    doc = _find_doc(doc_id)
    try:
        get_vectordb()._collection.delete(where={"doc_id": doc_id})
    except Exception as e:
        print("kb delete vectors failed:", e, file=sys.stderr)
    if doc is not None:
        _save_docs([d for d in _load_docs() if d["doc_id"] != doc_id])
    return doc


def move_document(doc_id: str, folder_id: str) -> dict:
    """移动文档到指定知识库：仅更新外键（注册表 + 已写入向量块的 metadata），不重新向量化"""
    if folder_id not in [f["id"] for f in _load_folders()]:
        raise ValueError("目标知识库不存在")
    doc = _find_doc(doc_id)
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
             doc_ids: Optional[List[str]] = None):
    """按语义相似度召回 top-k 资料块。

    folder_ids 不为空时只在这些知识库内检索（按需加载的核心：向量库保留全部数据，
    检索时用 Chroma where 过滤，勾选即时生效）；为空表示未勾选任何知识库，回退全库检索。
    doc_ids 不为空时进一步只检索这些文档（文件级勾选，与 folder_ids 取交集）。
    解析中/失败的文档没有向量块，天然不会被召回。
    """
    filter_cond = None
    if folder_ids and doc_ids:
        filter_cond = {"$and": [
            {"folder_id": {"$in": folder_ids}},
            {"doc_id": {"$in": doc_ids}},
        ]}
    elif folder_ids:
        filter_cond = {"folder_id": {"$in": folder_ids}}
    elif doc_ids:
        filter_cond = {"doc_id": {"$in": doc_ids}}

    if filter_cond:
        return get_vectordb().similarity_search(query, k=k, filter=filter_cond)
    return get_vectordb().similarity_search(query, k=k)


def doc_count() -> int:
    """知识库向量块总数（用于判断库是否为空，避免无谓的检索/embedding 调用）"""
    try:
        return get_vectordb()._collection.count()
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


def active_folder_ids() -> Optional[List[str]]:
    """当前激活（勾选）的知识库 id 列表；全部未勾选时返回 None（回退全库检索）"""
    ids = [x["id"] for x in _load_folders() if x.get("active")]
    return ids or None


def active_doc_ids() -> Optional[List[str]]:
    """当前激活（勾选）的文档 id 列表；全部未勾选时返回 None（不额外过滤）。
    与文件夹勾选叠加：文件夹未勾选则整库不检索，文件级勾选在文件夹基础上进一步收窄。
    """
    ids = [d["doc_id"] for d in _load_docs() if d.get("active", True)]
    return ids or None


def set_doc_active(doc_id: str, active: bool) -> dict:
    """切换单个文档的激活状态（是否参与检索）"""
    doc = _find_doc(doc_id)
    if doc is None:
        raise ValueError("文档不存在或已删除")
    doc["active"] = bool(active)
    _upsert_doc(doc)
    return {"doc_id": doc_id, "active": bool(active)}


# ==================== 知识库（文件夹）管理 ====================
# 数据模型：
#   Folder: {id, name, active, system, created_at} —— 存 kb_folders.json（激活状态持久化）
#   关系：Document.folder_id → Folder.id；知识库被删时其下文档退回系统“临时文件”区

def _system_folder() -> dict:
    return {
        "id": TEMP_FOLDER_ID,
        "name": TEMP_FOLDER_NAME,
        "active": True,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "system": True,
    }


def _load_folders() -> List[dict]:
    """读取知识库元数据；不存在时初始化系统“临时文件”知识库。
    旧版本中的“默认 / 未分类”会自动更名为“临时文件”。"""
    if not os.path.exists(FOLDERS_FILE):
        folders = [_system_folder()]
        with _folders_lock:
            with open(FOLDERS_FILE, "w", encoding="utf-8") as f:
                json.dump({"folders": folders}, f, ensure_ascii=False, indent=2)
        return folders
    with _folders_lock:
        with open(FOLDERS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    folders = data.get("folders") or []
    changed = False
    # 兜底：系统知识库必须存在且名称固定（误删/手工改坏/旧版名称时自愈）
    sys_folder = next((x for x in folders if x.get("id") == TEMP_FOLDER_ID), None)
    if sys_folder is None:
        folders.insert(0, _system_folder())
        changed = True
    else:
        sys_folder["system"] = True
        if sys_folder.get("name") != TEMP_FOLDER_NAME:
            sys_folder["name"] = TEMP_FOLDER_NAME
            changed = True
    if changed:
        _save_folders(folders)
    # 系统知识库始终排在最前
    folders.sort(key=lambda x: 0 if x.get("system") else 1)
    return folders


def _save_folders(folders: List[dict]) -> None:
    with _folders_lock:
        with open(FOLDERS_FILE, "w", encoding="utf-8") as f:
            json.dump({"folders": folders}, f, ensure_ascii=False, indent=2)


def list_folders() -> List[dict]:
    """知识库列表（含每个库的文件数与向量段数，供前端展示）"""
    folders = _load_folders()
    doc_n: dict = {}
    chunk_n: dict = {}
    for d in _load_docs():
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


def create_folder(name: str) -> dict:
    """新建知识库（重名校验）"""
    name = (name or "").strip()
    if not name:
        raise ValueError("知识库名称不能为空")
    folders = _load_folders()
    if any(x["name"] == name for x in folders):
        raise ValueError(f"已存在同名知识库：{name}")
    folder = {
        "id": uuid.uuid4().hex,
        "name": name,
        "active": True,  # 新建默认激活，立即参与检索
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "system": False,
    }
    folders.append(folder)
    _save_folders(folders)
    return folder


def _find_folder(folders: List[dict], folder_id: str) -> dict:
    for x in folders:
        if x["id"] == folder_id:
            return x
    raise ValueError("知识库不存在")


def rename_folder(folder_id: str, name: str) -> dict:
    """重命名知识库（系统“临时文件”库不可重命名）"""
    name = (name or "").strip()
    if not name:
        raise ValueError("知识库名称不能为空")
    folders = _load_folders()
    folder = _find_folder(folders, folder_id)
    if folder.get("system"):
        raise ValueError("系统临时文件库不可重命名")
    if any(x["name"] == name and x["id"] != folder_id for x in folders):
        raise ValueError(f"已存在同名知识库：{name}")
    folder["name"] = name
    _save_folders(folders)
    return folder


def set_folder_active(folder_id: str, active: bool) -> dict:
    """切换知识库激活状态（勾选=参与检索；状态持久化，重启不丢）"""
    folders = _load_folders()
    folder = _find_folder(folders, folder_id)
    folder["active"] = bool(active)
    _save_folders(folders)
    return folder


def delete_folder(folder_id: str) -> int:
    """删除知识库：其下所有文件退回系统“临时文件”库（只改外键 + 向量 metadata，
    数据安全优先，严禁级联删除文件与向量）。返回移动的文件数。"""
    if folder_id == TEMP_FOLDER_ID:
        raise ValueError("系统临时文件库不可删除")
    folders = _load_folders()
    _find_folder(folders, folder_id)  # 不存在则抛错

    # 先把文件外键改回临时文件区（须在删除知识库之前统计，避免 _load_docs 自愈抢先改归属）
    moved = 0
    docs = _load_docs()
    for d in docs:
        if d.get("folder_id") == folder_id:
            d["folder_id"] = TEMP_FOLDER_ID
            moved += 1
    if moved:
        _save_docs(docs)
    _save_folders([x for x in folders if x["id"] != folder_id])
    # 向量块 metadata 同步改归属
    try:
        _move_folder_chunks(folder_id, TEMP_FOLDER_ID)
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
