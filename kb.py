"""知识库（RAG）模块：文档入库、向量检索、文档管理。

基于 LangChain 生态实现：
- 切分：RecursiveCharacterTextSplitter（langchain-text-splitters）
- 向量化：OpenAIEmbeddings（langchain-openai，走 DashScope 兼容接口）
- 向量库：Chroma（langchain-chroma，本地持久化到 chroma_db/）
- PDF 解析：pypdf；TXT/MD 直接读文本

重依赖（chromadb 等）采用函数内延迟导入：只影响知识库功能，
不影响数据分析主流程的启动与运行。
"""
import os
import uuid
from typing import List, Tuple

from dotenv import load_dotenv

load_dotenv()

API_KEY = os.getenv("API_KEY")
BASE_URL = os.getenv("BASE_URL")
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "text-embedding-v3")
CHROMA_DIR = os.getenv(
    "CHROMA_DIR", os.path.join(os.path.dirname(os.path.abspath(__file__)), "chroma_db")
)

KB_EXTENSIONS = (".txt", ".md", ".pdf", ".csv")
MAX_KB_FILE_SIZE = 20 * 1024 * 1024  # 20MB
TOP_K = 4  # 每次提问召回的资料段数

_embeddings = None
_vectordb = None


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


def _read_text(path: str) -> str:
    """读取文本文件，自动尝试常见编码"""
    for enc in ("utf-8-sig", "utf-8", "gbk"):
        try:
            with open(path, "r", encoding=enc) as f:
                return f.read()
        except UnicodeDecodeError:
            continue
    raise ValueError("无法识别文件编码，请将文件另存为 UTF-8 后重试")


def _load_pages(path: str, ext: str) -> List[str]:
    """解析文档为文本页列表"""
    if ext == ".pdf":
        from pypdf import PdfReader

        reader = PdfReader(path)
        return [(page.extract_text() or "") for page in reader.pages]
    return [_read_text(path)]


def _get_splitter():
    from langchain_text_splitters import RecursiveCharacterTextSplitter

    # 中文文档：优先按段落/句子切，chunk 500 字、相邻块重叠 50 字
    return RecursiveCharacterTextSplitter(
        chunk_size=500,
        chunk_overlap=50,
        separators=["\n\n", "\n", "。", "！", "？", "；", "，", " ", ""],
    )


def ingest(path: str, ext: str, filename: str) -> dict:
    """文档入库：解析 → 切分 → 向量化 → 存入 Chroma"""
    doc_id = uuid.uuid4().hex
    pages = [p for p in _load_pages(path, ext) if p.strip()]
    if not pages:
        raise ValueError("未能从文件中提取到文本内容（扫描版/图片版 PDF 无法识别文字）")

    docs = _get_splitter().create_documents(pages)
    texts = [d.page_content.strip() for d in docs if d.page_content.strip()]
    metadatas = [
        {"doc_id": doc_id, "filename": filename, "path": path, "chunk": i}
        for i in range(len(texts))
    ]
    get_vectordb().add_texts(texts, metadatas=metadatas)
    return {"doc_id": doc_id, "filename": filename, "chunks": len(texts)}


def list_documents() -> List[dict]:
    """列出知识库中所有文档（按 doc_id 聚合块数）"""
    data = get_vectordb().get(include=["metadatas"])
    by_doc: dict = {}
    for meta in data.get("metadatas") or []:
        if not meta:
            continue
        did = meta.get("doc_id")
        if did not in by_doc:
            by_doc[did] = {
                "doc_id": did,
                "filename": meta.get("filename", ""),
                "path": meta.get("path", ""),
                "chunks": 0,
            }
        by_doc[did]["chunks"] += 1
    return list(by_doc.values())


def delete_document(doc_id: str) -> None:
    """按 doc_id 删除该文档的所有向量块"""
    get_vectordb().delete(where={"doc_id": doc_id})


def retrieve(query: str, k: int = TOP_K):
    """按语义相似度召回 top-k 资料块（LangChain Document 列表）"""
    return get_vectordb().similarity_search(query, k=k)


def doc_count() -> int:
    """知识库向量块总数（用于判断库是否为空，避免无谓的检索/embedding 调用）"""
    return get_vectordb()._collection.count()


def build_context(docs) -> Tuple[str, List[dict]]:
    """把召回的资料拼成 prompt 上下文，并整理出处列表"""
    parts, sources = [], []
    for i, d in enumerate(docs, 1):
        fname = d.metadata.get("filename", "未知文档")
        parts.append(f"[资料{i}]（来自 {fname}）\n{d.page_content}")
        sources.append({"filename": fname, "snippet": d.page_content.strip()[:160]})
    return "\n\n".join(parts), sources
