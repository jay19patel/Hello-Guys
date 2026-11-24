import hashlib

import pytest

from hello_guy_ai.services import rag
from hello_guy_ai.services.rag import RagService, TextChunker, VectorStore


def fake_embed(texts):
    """Deterministic 8-dim vectors: same text -> same vector."""
    return [[b / 255 + 0.01 for b in hashlib.sha256(t.lower().encode()).digest()[:8]] for t in texts]


@pytest.fixture
def service(tmp_path, monkeypatch):
    monkeypatch.setattr(rag, "embed_texts", fake_embed)
    return RagService(store=VectorStore(str(tmp_path / "m.db"), "chunks"))


def test_chunker_respects_size_and_overlap():
    text = ("Alpha beta gamma. " * 40) + "\n\n" + ("z" * 500)
    chunks = TextChunker(200, 40).split(text)
    assert len(chunks) > 3 and all(len(c) <= 200 for c in chunks)
    assert chunks[0][-20:] in chunks[1]
    assert TextChunker(200, 40).split("   ") == []
    with pytest.raises(ValueError):
        TextChunker(100, 100)


@pytest.mark.django_db
def test_ingest_search_scoped_by_knowledge_base(service, tmp_path):
    kb1 = KnowledgeBase.objects.create(slug="kb1", name="kb1")
    kb2 = KnowledgeBase.objects.create(slug="kb2", name="kb2")
    doc = Document.objects.create(knowledge_base=kb1, title="FAQ", content="x", status=Document.Status.READY)
    path = tmp_path / "faq.md"
    path.write_text("Refund policy: 30 days.\n\nShipping takes 5 days.")
    assert service.ingest(doc_id=doc.pk, kb_id=kb1.pk, title="FAQ", path=str(path)) == 1

    [chunk] = service.search([kb1.pk], path.read_text().strip(), top_k=3, min_score=0.5)
    assert (chunk.doc_id, chunk.title) == (doc.pk, "FAQ")
    assert service.search([kb2.pk], "Refund", top_k=3, min_score=-1) == []  # other KB isolated
    assert service.search([], "Refund", top_k=3, min_score=-1) == []  # empty scope = nothing, never "all"

    service.ingest(doc_id=doc.pk, kb_id=kb1.pk, title="FAQ", path=str(path))  # retry = no duplicates
    assert len(service.search([kb1.pk, kb2.pk], "x", top_k=10, min_score=-1)) == 1

    Document.objects.filter(pk=doc.pk).update(knowledge_base=kb2)  # moved: old vectors must not answer for kb1
    assert service.search([kb1.pk], "x", top_k=10, min_score=-1) == []
    service.delete(doc.pk)
    assert service.search([kb1.pk, kb2.pk], "x", top_k=10, min_score=-1) == []


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
    reopened = VectorStore(str(tmp_path / "m.db"), "chunks")
    assert len(reopened.search([1], fake_embed(["x"])[0], 5)) == 1


# ---- embedding model chosen in admin --------------------------------------------------------

from django.contrib.auth import get_user_model  # noqa: E402

from hello_guy_ai.models import Document, KnowledgeBase, LLMModel  # noqa: E402


def mistral_embed(**fields):
    llm = LLMModel(name="Mistral embed", kind=LLMModel.Kind.EMBEDDING, model_id="mistral/mistral-embed", **fields)
    llm.set_api_key("sk-test")
    llm.save()
    return llm


@pytest.mark.django_db
def test_admin_embedding_model_is_used_with_its_key(monkeypatch):
    assert rag.embedding_config()[0] == "ollama/nomic-embed-text"  # nothing in admin: settings fallback
    mistral_embed(is_default=True)
    sent = {}

    def fake_embedding(**kwargs):
        sent.update(kwargs)
        return type("R", (), {"data": [{"embedding": [0.1] * 1024} for _ in kwargs["input"]]})()

    monkeypatch.setattr(rag.litellm, "embedding", fake_embedding)
    assert len(rag.embed_texts(["hello"])[0]) == 1024
    assert sent["model"] == "mistral/mistral-embed" and sent["api_key"] == "sk-test"
    assert rag.collection_for("mistral/mistral-embed") == "hello_guy_chunks__mistral_mistral_embed"
    assert LLMModel.default() is None  # an embedding model never becomes the chat default


@pytest.mark.django_db
def test_each_embedding_model_gets_its_own_collection_sized_by_its_vectors(tmp_path):
    uri = str(tmp_path / "m.db")
    small, large = VectorStore(uri, "model_a"), VectorStore(uri, "model_b")
    small.replace_document(1, 1, "t", ["x"], [[0.1] * 8])
    large.replace_document(1, 1, "t", ["x"], [[0.1] * 1024])  # different dimension: no conflict
    assert len(large.search([1], [0.1] * 1024, 5)) == 1
    large.drop()
    assert large.search([1], [0.1] * 1024, 5) == [] and len(small.search([1], [0.1] * 8, 5)) == 1


@pytest.mark.django_db
def test_switching_embedding_model_in_admin_rebuilds_the_index(client, monkeypatch, django_capture_on_commit_callbacks):
    from hello_guy_ai.tasks import actors

    admin = get_user_model().objects.create_superuser("root", "r@x.co", "pw")
    client.force_login(admin)
    queued = []
    monkeypatch.setattr(actors.reindex_knowledge, "send", lambda: queued.append("reindex"))
    form = {
        "name": "Mistral embed", "kind": "embedding", "model_id": "mistral/mistral-embed",
        "api_key": "sk-test", "params": "{}", "enabled": "on", "is_default": "on",
    }
    with django_capture_on_commit_callbacks(execute=True):
        res = client.post("/admin/hello_guy_ai/llmmodel/add/", form)
    assert res.status_code == 302 and queued == ["reindex"]

    llm = LLMModel.objects.get()
    with django_capture_on_commit_callbacks(execute=True):  # only the key changed: same vectors, no rebuild
        client.post(f"/admin/hello_guy_ai/llmmodel/{llm.pk}/change/", {**form, "api_key": "sk-new"})
    assert queued == ["reindex"] and LLMModel.objects.get().get_api_key() == "sk-new"


@pytest.mark.django_db
def test_reindex_all_drops_and_requeues_every_document(monkeypatch, tmp_path):
    kb = KnowledgeBase.objects.create(slug="k", name="k")
    docs = [Document.objects.create(knowledge_base=kb, title=f"d{i}", content="x", status="ready") for i in range(2)]
    dropped, queued = [], []
    monkeypatch.setattr(VectorStore, "current", classmethod(lambda cls: type("S", (), {"drop": lambda s: dropped.append(1)})()))
    monkeypatch.setattr("hello_guy_ai.services.chat.enqueue_ingest", queued.append)
    assert rag.reindex_all() == 2 and dropped == [1] and sorted(queued) == sorted(d.pk for d in docs)
    assert set(Document.objects.values_list("status", flat=True)) == {"pending"}


@pytest.mark.django_db
def test_probe_command_shows_scores_and_checks_expectations(monkeypatch, tmp_path):
    from django.core.management import CommandError, call_command

    kb = KnowledgeBase.objects.create(slug="k", name="k")
    Document.objects.create(knowledge_base=kb, title="ERP services", content="ERPNext setup", status="ready")
    Document.objects.create(knowledge_base=kb, title="AI chatbots", content="LLM assistants", status="ready")
    vectors = {"ERPNext setup": [1, 0], "LLM assistants": [0, 1], "erp?": [0.9, 0.1], "bots?": [0.1, 0.9]}
    monkeypatch.setattr(
        "hello_guy_ai.management.commands.hello_guy_rag_probe.embed_texts", lambda texts: [vectors[t] for t in texts]
    )
    questions = tmp_path / "q.txt"
    questions.write_text("erp? => ERP services\n# comment\nbots? => ERP services\n")
    out = __import__("io").StringIO()
    with pytest.raises(CommandError, match="1 expected match"):
        call_command("hello_guy_rag_probe", "--file", str(questions), stdout=out)
    assert "PASS" in out.getvalue() and "FAIL: wanted 'ERP services'" in out.getvalue()
