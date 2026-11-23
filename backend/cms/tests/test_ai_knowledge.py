"""cms/ai_knowledge.py + cms/ai_assistants.py on top of hello_guy_ai: what the website bot may learn."""

import pytest
from django.contrib.contenttypes.models import ContentType

from cms.models import FAQ, BlogPost
from hello_guy_ai import knowledge
from hello_guy_ai.models import Document, KnowledgeBase
from hello_guy_ai.services import chat
from hello_guy_ai.tests.test_rag import mistral_embed

pytestmark = pytest.mark.django_db


def doc_for(obj):
    return Document.objects.filter(content_type=ContentType.objects.get_for_model(obj), object_id=str(obj.pk)).first()


def test_cms_sources_index_only_public_content(monkeypatch):
    monkeypatch.setattr(chat, "enqueue_ingest", lambda pk: None)
    live = BlogPost.objects.create(title="AI Chatbots", slug="ai-chatbots", content="How we build them.")
    draft = BlogPost.objects.create(title="Draft", slug="draft", content="Secret", is_published=False)
    FAQ.objects.create(question="Do you do ERP?", answer="Yes, ERPNext and custom.")
    knowledge.reconcile()
    assert doc_for(live).knowledge_base.slug == "company-public" and doc_for(draft) is None
    faq = Document.objects.get(content_type__model="faq")
    assert "Q: Do you do ERP?" in faq.content and "A: Yes, ERPNext" in faq.content
    assert "cms.inquirysubmission" not in knowledge.SOURCES  # visitor PII never goes to the vector store


@pytest.fixture
def live_sync(monkeypatch):
    """Signals -> on_commit -> queue, with the queue running the sync inline (what the worker does)."""
    from hello_guy_ai.tasks import actors

    jobs = []

    def run(label, pk):
        jobs.append((label, pk))
        knowledge.sync_object(label, pk)

    monkeypatch.setattr(actors.sync_knowledge_record, "send", run)
    monkeypatch.setattr(chat, "enqueue_ingest", lambda pk: None)
    return jobs


def test_any_change_updates_the_index_through_signals(live_sync, django_capture_on_commit_callbacks):
    with django_capture_on_commit_callbacks(execute=True):
        post = BlogPost.objects.create(title="RAG guide", slug="rag", content="Chunk well.")
    assert live_sync == [("cms.blogpost", str(post.pk))] and "Chunk well." in doc_for(post).content

    with django_capture_on_commit_callbacks(execute=True):
        post.content = "Chunk smarter."
        post.save()
    assert "Chunk smarter." in doc_for(post).content

    with django_capture_on_commit_callbacks(execute=True):
        post.is_published = False  # unpublish = gone from the assistant's knowledge
        post.save()
    assert doc_for(post) is None

    with django_capture_on_commit_callbacks(execute=True):
        post.is_published = True
        post.save()
    with django_capture_on_commit_callbacks(execute=True):
        post.delete()
    assert not Document.objects.filter(content_type__model="blogpost").exists()


def test_editing_a_child_reindexes_its_parent(live_sync, django_capture_on_commit_callbacks):
    from cms.models import Project, ProjectSection

    with django_capture_on_commit_callbacks(execute=True):
        project = Project.objects.create(slug="crm", title="CRM", description="A CRM.")
    with django_capture_on_commit_callbacks(execute=True):
        section = ProjectSection.objects.create(project=project, title="Stack", content="Django + Redis")
    assert live_sync[-1] == ("cms.project", str(project.pk)) and "Django + Redis" in doc_for(project).content

    with django_capture_on_commit_callbacks(execute=True):
        section.delete()
    assert "Django + Redis" not in doc_for(project).content


def test_rolled_back_save_queues_nothing(live_sync, django_capture_on_commit_callbacks):
    from django.db import transaction

    with django_capture_on_commit_callbacks(execute=True):
        try:
            with transaction.atomic():
                BlogPost.objects.create(title="Oops", slug="oops", content="x")
                raise RuntimeError
        except RuntimeError:
            pass
    assert live_sync == []


def test_demo_company_seed_is_idempotent_and_fully_indexable(monkeypatch):
    from django.core.management import call_command

    from cms.models import Solution, Testimonial

    monkeypatch.setattr(chat, "enqueue_ingest", lambda pk: None)
    call_command("seed_demo_company", stdout=__import__("io").StringIO())
    call_command("seed_demo_company", stdout=__import__("io").StringIO())  # again: updates, no duplicates
    assert Solution.objects.count() == 6 and FAQ.objects.count() == 10
    assert all(t.name.startswith("Demo client") for t in Testimonial.objects.all())  # never passed off as real

    knowledge.reconcile()
    by_model = {m: Document.objects.filter(content_type__model=m).count() for m in ("solution", "project", "faq")}
    assert by_model == {"solution": 6, "project": 4, "faq": 10}
    erp = Document.objects.get(title="ERPNext Implementation")
    assert erp.knowledge_base.slug == "company-public" and "Tally" in erp.content


@pytest.mark.django_db
def test_calibrated_threshold_of_the_embedding_model_wins(django_user_model):
    from hello_guy_ai.services.agents import resolve_agent

    staff = django_user_model.objects.create_user("s", is_staff=True)
    KnowledgeBase.objects.create(slug="company-public", name="Company")
    assert resolve_agent("website_assistant", staff).rag_min_score == 0.3  # assistant default
    mistral_embed(is_default=True, min_score=0.7)
    assert resolve_agent("website_assistant", staff).rag_min_score == 0.7