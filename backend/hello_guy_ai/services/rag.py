"""RAG pipeline: load -> chunk -> embed (LiteLLM) -> Milvus.

The embedding model is the admin's default "Embedding" LLM model (else settings EMBEDDING_MODEL). Each
embedding model gets its own Milvus collection, created with the dimension of its first vectors, so
switching models never mixes incompatible vectors; the switch re-indexes every document (reindex_all).

Milvus is touched only by the worker process. With Milvus Lite (a file path URI)
run the worker as ONE process (threads are fine); point MILVUS_URI at a
Milvus server to scale out.
"""

import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import litellm
from pymilvus import DataType, MilvusClient

from hello_guy_ai.conf import ai_settings

logger = logging.getLogger(__name__)

SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf"}
MAX_TEXT_BYTES = 60000  # Milvus VARCHAR max is 65535 bytes


@dataclass(frozen=True)
class Chunk:
    doc_id: int
    title: str
    text: str
    score: float


def embedding_config() -> tuple[str, dict[str, Any]]:
    """(model id, litellm kwargs) of the embedding model in use."""
    from hello_guy_ai.models import LLMModel

    llm = LLMModel.default(LLMModel.Kind.EMBEDDING)
    if llm is not None:
        return llm.model_id, llm.connection_kwargs()
    return ai_settings.EMBEDDING_MODEL, (
        {"api_base": ai_settings.EMBEDDING_API_BASE} if ai_settings.EMBEDDING_API_BASE else {}
    )


def embedding_min_score() -> float | None:
    """The similarity threshold calibrated for the embedding model in use (None: not set)."""
    from hello_guy_ai.models import LLMModel

    llm = LLMModel.default(LLMModel.Kind.EMBEDDING)
    return llm.min_score if llm else None


def collection_for(model_id: str) -> str:
    """One Milvus collection per embedding model, e.g. hello_guy_chunks__mistral_mistral_embed."""
    return f"{ai_settings.MILVUS_COLLECTION}__{re.sub(r'[^0-9A-Za-z]', '_', model_id)}"[:255]


class RagService:
    """Public entry point used by tasks and the agent engine."""

    def __init__(self, store: "VectorStore | None" = None) -> None:
        self.store = store or VectorStore.current()

    def ingest(self, *, doc_id: int, kb_id: int, title: str, path: str = "", text: str = "") -> int:
        """Index a file (path) or ready text (synced database records)."""
        chunks = TextChunker(ai_settings.CHUNK_SIZE, ai_settings.CHUNK_OVERLAP).split(text or load_text(path))
        if not chunks:
            raise ValueError("Document has no extractable text")
        vectors = embed_texts(chunks)
        self.store.replace_document(doc_id, kb_id, title, chunks, vectors)
        return len(chunks)

    def delete(self, doc_id: int) -> None:
        self.store.delete_document(doc_id)

    def search(self, kb_ids: list[int], query: str, top_k: int, min_score: float) -> list[Chunk]:
        if not kb_ids:  # fail closed: an empty scope never means "search everything"
            return []
        [vector] = embed_texts([query])
        return [c for c in self.store.search(kb_ids, vector, top_k) if c.score >= min_score]


class VectorStore:
    FIELDS = ("doc_id", "title", "text")

    _clients: dict[str, MilvusClient] = {}
    _lock = threading.Lock()

    def __init__(self, uri: str, collection: str, token: str = "") -> None:
        self.client = self._client(uri, token)
        self.collection = collection
        if self.client.has_collection(collection):
            # After a restart/crash the collection exists but may be "released"; search needs it loaded.
            self.client.load_collection(collection)

    @classmethod
    def _client(cls, uri: str, token: str) -> MilvusClient:
        # One client per process and URI: Milvus Lite allows one opener per DB file.
        with cls._lock:
            if uri not in cls._clients:
                if "://" not in uri:
                    Path(uri).parent.mkdir(parents=True, exist_ok=True)
                cls._clients[uri] = MilvusClient(uri=uri, token=token or "")
            return cls._clients[uri]

    @classmethod
    def current(cls) -> "VectorStore":
        """The store of the embedding model in use right now."""
        model_id, _ = embedding_config()
        return cls(ai_settings.MILVUS_URI, collection_for(model_id), ai_settings.MILVUS_TOKEN)

    def replace_document(
        self, doc_id: int, kb_id: int, title: str, chunks: list[str], vectors: list[list[float]]
    ) -> None:
        self._ensure_collection(len(vectors[0]))
        # Delete-then-insert keeps re-ingestion (task retries) idempotent.
        self.delete_document(doc_id)
        rows = [
            {
                "doc_id": doc_id,
                "kb_id": int(kb_id),
                "title": title[:500],
                "text": _truncate_bytes(text),
                "vector": vector,
            }
            for text, vector in zip(chunks, vectors, strict=True)
        ]
        self.client.insert(self.collection, rows)

    def delete_document(self, doc_id: int) -> None:
        if self.client.has_collection(self.collection):
            self.client.delete(self.collection, filter=f"doc_id == {int(doc_id)}")

    def search(self, kb_ids: list[int], vector: list[float], top_k: int) -> list[Chunk]:
        if not kb_ids or not self.client.has_collection(self.collection):
            return []
        scope = ", ".join(str(int(k)) for k in kb_ids)  # int() = no filter-expression injection
        hits = self.client.search(
            self.collection,
            data=[vector],
            filter=f"kb_id in [{scope}]",
            limit=top_k,
            output_fields=list(self.FIELDS),
        )[0]
        return [
            Chunk(
                doc_id=h["entity"]["doc_id"], title=h["entity"]["title"], text=h["entity"]["text"], score=h["distance"]
            )
            for h in hits
        ]

    def drop(self) -> None:
        if self.client.has_collection(self.collection):
            self.client.drop_collection(self.collection)

    def _ensure_collection(self, dim: int) -> None:
        if self.client.has_collection(self.collection):
            return
        schema = self.client.create_schema(auto_id=True)
        schema.add_field("id", DataType.INT64, is_primary=True)
        schema.add_field("doc_id", DataType.INT64)
        schema.add_field("kb_id", DataType.INT64)
        schema.add_field("title", DataType.VARCHAR, max_length=512)
        schema.add_field("text", DataType.VARCHAR, max_length=65535)
        schema.add_field("vector", DataType.FLOAT_VECTOR, dim=dim)
        index = self.client.prepare_index_params()
        index.add_index(field_name="vector", index_type="AUTOINDEX", metric_type="COSINE")
        self.client.create_collection(self.collection, schema=schema, index_params=index)
        logger.info("Created Milvus collection %s (dim=%s)", self.collection, dim)


class TextChunker:
    """Recursive splitter: paragraphs -> lines -> sentences -> words, with overlap."""

    SEPARATORS = ("\n\n", "\n", ". ", " ")

    def __init__(self, size: int, overlap: int) -> None:
        if overlap >= size:
            raise ValueError("CHUNK_OVERLAP must be smaller than CHUNK_SIZE")
        self.size = size
        self.overlap = overlap

    def split(self, text: str) -> list[str]:
        return [c.strip() for c in self._merge(self._pieces(text, 0)) if c.strip()]

    def _pieces(self, text: str, level: int) -> list[str]:
        if len(text) <= self.size:
            return [text]
        if level >= len(self.SEPARATORS):
            return [text[i : i + self.size] for i in range(0, len(text), self.size)]
        sep = self.SEPARATORS[level]
        parts = text.split(sep)
        parts = [p + sep for p in parts[:-1]] + [parts[-1]]  # keep separators so text stays intact
        out: list[str] = []
        for part in parts:
            out.extend(self._pieces(part, level + 1))
        return out

    def _merge(self, pieces: list[str]) -> list[str]:
        chunks: list[str] = []
        current = ""
        for piece in pieces:
            if len(current) + len(piece) <= self.size:
                current += piece
                continue
            if current.strip():
                chunks.append(current)
            tail = current[-self.overlap :] if self.overlap else ""
            current = tail + piece if len(tail) + len(piece) <= self.size else piece
        if current.strip():
            chunks.append(current)
        return chunks


def load_text(path: str) -> str:
    suffix = Path(path).suffix.lower()
    if suffix not in SUPPORTED_EXTENSIONS:
        raise ValueError(f"Unsupported file type: {suffix}")
    if suffix == ".pdf":
        from pypdf import PdfReader

        return "\n\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
    return Path(path).read_text(encoding="utf-8", errors="replace")


def embed_texts(texts: list[str]) -> list[list[float]]:
    model, kwargs = embedding_config()
    size = ai_settings.EMBEDDING_BATCH_SIZE
    vectors: list[list[float]] = []
    for start in range(0, len(texts), size):
        response = litellm.embedding(
            model=model,
            input=texts[start : start + size],
            timeout=ai_settings.LLM_TIMEOUT_SECONDS,
            num_retries=ai_settings.LLM_NUM_RETRIES,
            **kwargs,
        )
        vectors.extend(item["embedding"] for item in response.data)
    return vectors


def reindex_all() -> int:
    """Rebuild the current embedding model's collection from every document. Runs in the worker."""
    from hello_guy_ai.models import Document
    from hello_guy_ai.services.chat import enqueue_ingest

    VectorStore.current().drop()  # a clean rebuild: no vectors of deleted documents survive
    ids = list(Document.objects.values_list("pk", flat=True))
    Document.objects.filter(pk__in=ids).update(status=Document.Status.PENDING)
    for pk in ids:
        enqueue_ingest(pk)
    return len(ids)


def _truncate_bytes(text: str) -> str:
    return text.encode("utf-8")[:MAX_TEXT_BYTES].decode("utf-8", errors="ignore")
