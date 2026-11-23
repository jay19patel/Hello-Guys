"""Database records -> knowledge base sync (hello_guy_ai.knowledge), against the demo_crm test app."""

import pytest
from django.contrib.contenttypes.models import ContentType

from hello_guy_ai import knowledge
from hello_guy_ai.models import Document
from hello_guy_ai.services import chat
from hello_guy_ai.tests.demo_crm.models import Lead

pytestmark = pytest.mark.django_db


class WonLeadSource(knowledge.KnowledgeSource):
    model = "demo_crm.lead"
    knowledge_base = "crm-notes"

    def get_queryset(self):
        return Lead.objects.filter(status="won")

    def get_title(self, obj):
        return obj.name

    def get_text(self, obj):
        return f"{obj.name} from {obj.company} signed a deal."


knowledge.SOURCES.pop("demo_crm.lead")  # registered per test only (below), never globally


@pytest.fixture
def ingested(monkeypatch):
    monkeypatch.setitem(knowledge.SOURCES, "demo_crm.lead", WonLeadSource())
    queued = []
    monkeypatch.setattr(chat, "enqueue_ingest", queued.append)
    return queued


def doc_for(obj):
    return Document.objects.filter(content_type=ContentType.objects.get_for_model(obj), object_id=str(obj.pk)).first()


def test_sync_indexes_skips_unchanged_and_removes_unpublished(ingested):
    lead = Lead.objects.create(name="Acme", company="Acme Ltd", status="won")
    knowledge.sync_object("demo_crm.lead", lead.pk)
    doc = doc_for(lead)
    assert doc.knowledge_base.slug == "crm-notes" and not doc.file
    assert doc.content == "Lead: Acme\n\nAcme from Acme Ltd signed a deal." and ingested == [doc.pk]

    Document.objects.filter(pk=doc.pk).update(status=Document.Status.READY)
    knowledge.sync_object("demo_crm.lead", lead.pk)
    assert ingested == [doc.pk]  # same text: not embedded again

    lead.company = "Acme Global"
    lead.save()
    knowledge.sync_object("demo_crm.lead", lead.pk)
    assert len(ingested) == 2 and "Acme Global" in doc_for(lead).content

    Lead.objects.filter(pk=lead.pk).update(status="lost")  # left the queryset (like unpublishing a post)
    knowledge.sync_object("demo_crm.lead", lead.pk)
    assert doc_for(lead) is None


def test_reconcile_adds_missing_and_removes_orphans(ingested, monkeypatch):
    kept = Lead.objects.create(name="Kept", status="won")
    gone = Lead.objects.create(name="Gone", status="won")
    knowledge.reconcile()
    assert doc_for(kept) and doc_for(gone)

    gone.delete()
    knowledge.reconcile()
    assert doc_for(kept) and Document.objects.filter(object_id=str(gone.pk), content_type__model="lead").count() == 0

    monkeypatch.delitem(knowledge.SOURCES, "demo_crm.lead")  # e.g. the app was renamed: its old docs are orphans
    knowledge.reconcile()
    assert doc_for(kept) is None


def test_text_documents_ingest_without_a_file(monkeypatch):
    from hello_guy_ai.services.rag import RagService

    captured = {}

    class Store:
        def replace_document(self, doc_id, kb_id, title, chunks, vectors):
            captured.update(doc_id=doc_id, chunks=chunks)

    monkeypatch.setattr("hello_guy_ai.services.rag.embed_texts", lambda texts: [[0.0]] * len(texts))
    count = RagService(store=Store()).ingest(doc_id=7, kb_id=1, title="t", text="Hello world")
    assert count == 1 and captured == {"doc_id": 7, "chunks": ["Hello world"]}


def test_fields_shortcut_builds_text_and_skips_empty():
    class LeadFields(knowledge.KnowledgeSource):
        model = "demo_crm.lead_fields_test"  # unique key: never touches the real registry entry
        knowledge_base = "crm-notes"
        fields = ["name", "company", "tags"]

    knowledge.SOURCES.pop("demo_crm.lead_fields_test")
    lead = Lead(name="Acme", company="", tags="vip")
    assert LeadFields().get_text(lead) == "Name: Acme\nTags: vip"
