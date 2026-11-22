"""Dramatiq actors. Worker: `dramatiq hello_guy_ai.worker` (run from backend/)."""

import logging

import dramatiq
from django.utils import timezone

from hello_guy_ai import triggers
from hello_guy_ai.conf import ai_settings
from hello_guy_ai.engine import AgentRunner
from hello_guy_ai.models import Document, Run
from hello_guy_ai.rag import RagService

logger = logging.getLogger(__name__)

CHAT_QUEUE = "hello_guy_ai_chat"
INGEST_QUEUE = "hello_guy_ai_ingest"
TRIGGER_QUEUE = "hello_guy_ai_triggers"
RUN_LIMIT_MS = ai_settings.RUN_TIME_LIMIT_SECONDS * 1000


def _load_run(run_id: str) -> Run | None:
    return Run.objects.select_related("thread", "thread__created_by").filter(pk=run_id).first()


# max_retries=0 on runs: a retry would duplicate saved messages and re-run tools.
# LLM-level retries happen inside LiteLLM (num_retries). Status CAS makes duplicates harmless anyway.
@dramatiq.actor(queue_name=CHAT_QUEUE, max_retries=0, time_limit=RUN_LIMIT_MS)
def run_assistant(run_id: str) -> None:
    run = _load_run(run_id)
    if run is not None:
        AgentRunner(run).execute()


@dramatiq.actor(queue_name=CHAT_QUEUE, max_retries=0, time_limit=RUN_LIMIT_MS)
def resume_assistant(run_id: str) -> None:
    run = _load_run(run_id)
    if run is not None:
        AgentRunner(run).resume()


# Triggers run unattended agents; one failure must not re-fire side effects, so no retries.
@dramatiq.actor(queue_name=TRIGGER_QUEUE, max_retries=0, time_limit=RUN_LIMIT_MS)
def fire_trigger(trigger_id: int, object_pk: str | None = None, snapshot: dict | None = None) -> None:
    triggers.fire(trigger_id, object_pk, snapshot)


# Ingestion is idempotent (delete-then-insert), so retries are safe.
@dramatiq.actor(queue_name=INGEST_QUEUE, max_retries=3, min_backoff=5000, time_limit=30 * 60 * 1000)
def ingest_document(document_id: int) -> None:
    doc = Document.objects.select_related("knowledge_base").filter(pk=document_id).first()
    if doc is None:
        return
    Document.objects.filter(pk=doc.pk).update(status=Document.Status.PROCESSING, error="")
    try:
        count = RagService().ingest(doc_id=doc.pk, kb_id=doc.knowledge_base_id, title=doc.title, path=doc.file.path)
    except ValueError as exc:  # bad file / bad config: retrying won't help
        Document.objects.filter(pk=doc.pk).update(status=Document.Status.FAILED, error=str(exc)[:2000])
        return
    except Exception as exc:
        Document.objects.filter(pk=doc.pk).update(status=Document.Status.FAILED, error=str(exc)[:2000])
        raise  # transient (Ollama/Milvus down): let Dramatiq retry
    Document.objects.filter(pk=doc.pk).update(
        status=Document.Status.READY, chunk_count=count, updated_at=timezone.now()
    )


@dramatiq.actor(queue_name=INGEST_QUEUE, max_retries=5, min_backoff=5000)
def delete_document_vectors(document_id: int) -> None:
    RagService().delete(document_id)
