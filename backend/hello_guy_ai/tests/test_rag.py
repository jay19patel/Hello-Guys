import hashlib

import pytest

from hello_guy_ai import rag
from hello_guy_ai.rag import RagService, TextChunker, VectorStore


def fake_embed(texts):
    """Deterministic 8-dim vectors: same text -> same vector."""
    return [[b / 255 + 0.01 for b in hashlib.sha256(t.lower().encode()).digest()[:8]] for t in texts]


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr(rag, "embed_texts", fake_embed)
    return RagService(store=VectorStore(str(tmp_path / "m.db"), "chunks", dim=8))


def test_chunker_respects_size_and_overlap():
    text = ("Alpha beta gamma. " * 40) + "\n\n" + ("z" * 500)
    chunks = TextChunker(200, 40).split(text)
    assert len(chunks) > 3 and all(len(c) <= 200 for c in chunks)
    assert chunks[0][-20:] in chunks[1]
    assert TextChunker(200, 40).split("   ") == []
    with pytest.raises(ValueError):
        TextChunker(100, 100)


def test_ingest_search_scoped_by_knowledge_base(service, tmp_path):
    path = tmp_path / "faq.md"
    path.write_text("Refund policy: 30 days.\n\nShipping takes 5 days.")
    assert service.ingest(doc_id=7, kb_id=1, title="FAQ", path=str(path)) == 1

    [chunk] = service.search([1], path.read_text().strip(), top_k=3, min_score=0.5)
    assert (chunk.doc_id, chunk.title) == (7, "FAQ")
    assert service.search([2], "Refund", top_k=3, min_score=-1) == []  # other KB isolated
    assert service.search([], "Refund", top_k=3, min_score=-1) == []  # empty scope = nothing, never "all"

    service.ingest(doc_id=7, kb_id=1, title="FAQ", path=str(path))  # retry = no duplicates
    assert len(service.search([1, 2], "x", top_k=10, min_score=-1)) == 1
    service.delete(7)
    assert service.search([1], "x", top_k=10, min_score=-1) == []


def test_rejects_unsupported_file(service, tmp_path):
    bad = tmp_path / "a.exe"
    bad.write_text("x")
    with pytest.raises(ValueError):
        service.ingest(doc_id=1, kb_id=1, title="a", path=str(bad))


def test_reopened_store_loads_released_collection(service, tmp_path):
    path = tmp_path / "a.md"
    path.write_text("Some knowledge.")
    service.ingest(doc_id=1, kb_id=1, title="A", path=str(path))
    service.store.client.release_collection("chunks")  # what a crash/restart leaves behind
    reopened = VectorStore(str(tmp_path / "m.db"), "chunks", dim=8)
    assert len(reopened.search([1], fake_embed(["x"])[0], 5)) == 1
