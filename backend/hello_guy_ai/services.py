"""Use cases shared by the API, admin, triggers and code. All permission checks live here."""

import logging
from datetime import timedelta
from typing import Any

from django.db import IntegrityError, transaction
from django.db.models import QuerySet
from django.urls import reverse
from django.utils import timezone

from hello_guy_ai import events
from hello_guy_ai.agents import list_agents
from hello_guy_ai.assistant import AIAssistant
from hello_guy_ai.conf import ai_settings
from hello_guy_ai.exceptions import AssistantNotFound, NotAllowed, RunConflict, ThreadBusy
from hello_guy_ai.memory import save_feedback_memory
from hello_guy_ai.models import Document, Message, Run, Source, Thread, Trigger

logger = logging.getLogger(__name__)

MAX_MESSAGE_CHARS = 8000
MAX_ANSWER_CHARS = 2000
MAX_FEEDBACK_CHARS = 500


# ---- agents & threads -----------------------------------------------------------


def list_assistants(user: Any) -> list[dict]:
    return list_agents(user)


def check_can_run(user: Any, agent_id: str) -> None:
    """Cheap permission check for the request path (no tool/KB assembly)."""
    if not AIAssistant.get_cls(agent_id).can_run(user):  # get_cls raises AssistantNotFound
        raise NotAllowed("You are not allowed to use this assistant")


def threads_for(user: Any, assistant_id: str | None = None, source: str | None = Source.CHAT) -> QuerySet[Thread]:
    qs = Thread.objects.all() if user.is_superuser else Thread.objects.filter(created_by=user)
    if source:
        qs = qs.filter(source=source)
    return qs.filter(assistant_id=assistant_id) if assistant_id else qs


def get_thread(user: Any, thread_id: Any) -> Thread:
    """Raises Thread.DoesNotExist for missing AND foreign threads (no existence leak)."""
    return threads_for(user, source=None).get(pk=thread_id)


def get_run(user: Any, run_id: Any) -> Run:
    return Run.objects.select_related("thread").get(pk=run_id, thread__in=threads_for(user, source=None))


def chat_messages(thread: Thread) -> QuerySet[Message]:
    """What a chat shows: user/assistant text with how each run ended. Tool rows stay internal."""
    return thread.messages.select_related("run").exclude(role=Message.Role.TOOL).exclude(content="")


def active_run(thread: Thread) -> Run | None:
    return thread.runs.filter(status__in=Run.ACTIVE).first()


def create_thread(user: Any, assistant_id: str, name: str = "") -> Thread:
    check_can_run(user, assistant_id)
    return Thread.objects.create(created_by=user, assistant_id=assistant_id, name=name[:255])


def stream_url(run: Run) -> str:
    url = reverse("hello_guy_ai_run_stream", args=[run.pk])
    return f"{url}?after={run.stream_offset}" if run.stream_offset else url


# ---- public website chat ---------------------------------------------------------------


def public_user() -> Any:
    """The user every anonymous website visitor acts as. Raises AssistantNotFound when disabled."""
    from django.contrib.auth import get_user_model

    if not (ai_settings.PUBLIC_AGENT and ai_settings.PUBLIC_USER):
        raise AssistantNotFound("Website chat is not enabled")
    user = get_user_model().objects.filter(username=ai_settings.PUBLIC_USER, is_active=True).first()
    if user is None:
        raise AssistantNotFound("Website chat is not set up (run: manage.py setup_website_bot)")
    return user


def public_threads() -> QuerySet[Thread]:
    """Only threads of the public agent owned by the website user: a visitor can never reach others."""
    return Thread.objects.filter(created_by=public_user(), assistant_id=ai_settings.PUBLIC_AGENT)


# ---- turns -------------------------------------------------------------------------


def post_message(user: Any, thread: Thread, content: str) -> tuple[Message, Run]:
    """Save the user message and queue a background run. One active run per thread."""
    content = _clean_text(content, MAX_MESSAGE_CHARS, "Message")
    check_can_run(user, thread.assistant_id)
    _fail_stale_runs(thread)
    blocking = active_run(thread)
    if blocking and blocking.status == Run.Status.PAUSED:
        raise ThreadBusy("An action is waiting for your approval. Approve, deny or stop it first.")
    try:
        with transaction.atomic():
            run, message = _create_turn(thread, content)
            transaction.on_commit(lambda: _enqueue("run_assistant", run.pk))
    except IntegrityError:
        raise ThreadBusy("Assistant is still answering the previous message") from None
    return message, run


def start_agent_run(
    user: Any, agent_id: str, prompt: str, *, trigger: Trigger | None = None, auto_approve: bool = False
) -> Run:
    """Create a thread + queued run for a non-chat caller (triggers, code). Caller executes it."""
    check_can_run(user, agent_id)
    source = Source.TRIGGER if trigger else Source.CHAT
    name = f"⚡ {trigger.name}" if trigger else prompt[:60]
    with transaction.atomic():
        thread = Thread.objects.create(created_by=user, assistant_id=agent_id, name=name, source=source)
        run, _ = _create_turn(thread, prompt, source=source, trigger=trigger, auto_approve=auto_approve)
    return run


def ask(user: Any, agent_id: str, prompt: str, *, auto_approve: bool = False) -> Run:
    """Run an agent synchronously from code or the shell (Flow: Agent.run). Returns the finished Run."""
    from hello_guy_ai.engine import AgentRunner

    run = start_agent_run(user, agent_id, prompt, auto_approve=auto_approve)
    AgentRunner(run).execute()
    run.refresh_from_db()
    return run


def resume_run(user: Any, run: Run, answers: dict[str, str]) -> Run:
    """Answer a paused run's pending approvals: 'approve', 'deny', or free-text feedback."""
    if run.status != Run.Status.PAUSED:
        raise RunConflict(f"Only paused runs can be resumed (this run is {run.status}).")
    check_can_run(user, run.thread.assistant_id)
    pending = {q["key"] for q in run.questions or []}
    if set(answers) != pending:
        raise ValueError(f"Answer every pending action exactly once: {sorted(pending)}")
    clean = {key: _clean_text(value, MAX_ANSWER_CHARS, "Answer") for key, value in answers.items()}
    offset = events.last_event_id(run.pk)
    claimed = Run.objects.filter(pk=run.pk, status=Run.Status.PAUSED).update(
        status=Run.Status.QUEUED, answers=clean, stream_offset=offset, last_active_at=timezone.now()
    )
    if not claimed:  # double click / two tabs
        raise RunConflict("This run was already resumed or stopped.")
    transaction.on_commit(lambda: _enqueue("resume_assistant", run.pk))
    run.refresh_from_db()
    return run


def stop_run(user: Any, run: Run) -> str:
    if run.status == Run.Status.PAUSED:
        done = Run.objects.filter(pk=run.pk, status=Run.Status.PAUSED).update(
            status=Run.Status.STOPPED, error="Stopped by user", finished_at=timezone.now(), questions=None
        )
        if done:
            events.RunEventPublisher(run.pk).publish("stopped", {})
        return Run.Status.STOPPED
    if run.status == Run.Status.QUEUED:
        if Run.objects.filter(pk=run.pk, status=Run.Status.QUEUED).update(
            status=Run.Status.STOPPED, error="Stopped by user", finished_at=timezone.now()
        ):
            events.RunEventPublisher(run.pk).publish("stopped", {})
            return Run.Status.STOPPED
    if run.status in Run.LIVE:
        events.request_stop(run.pk)  # the worker notices within a few tokens
        return "stopping"
    return run.status


def submit_feedback(user: Any, run: Run, rating: str, comment: str = "") -> dict:
    if rating not in ("up", "down", "none"):
        raise ValueError("rating must be up, down or none")
    if not run.is_finished:
        raise RunConflict("Feedback applies to finished runs only.")
    comment = (comment or "").strip()
    if len(comment) > MAX_FEEDBACK_CHARS:
        raise ValueError(f"Keep feedback under {MAX_FEEDBACK_CHARS} characters")
    Run.objects.filter(pk=run.pk).update(
        feedback_rating="" if rating == "none" else rating, feedback_comment="" if rating == "none" else comment
    )
    memory_id = save_feedback_memory(run, user, comment) if rating == "down" and comment else None
    return {"rating": None if rating == "none" else rating, "memory_id": memory_id}


# ---- knowledge ---------------------------------------------------------------------


def enqueue_ingest(doc_id: int) -> None:
    from hello_guy_ai.tasks import ingest_document

    try:
        ingest_document.send(doc_id)
    except Exception:
        logger.exception("Could not enqueue document %s", doc_id)
        Document.objects.filter(pk=doc_id).update(status=Document.Status.FAILED, error="Could not enqueue")


# ---- internals -------------------------------------------------------------------


def _create_turn(thread: Thread, content: str, **run_fields: Any) -> tuple[Run, Message]:
    run = Run.objects.create(thread=thread, **run_fields)
    message = Message.objects.create(thread=thread, run=run, role=Message.Role.USER, content=content)
    Thread.objects.filter(pk=thread.pk).update(updated_at=timezone.now(), name=thread.name or content[:60])
    return run, message


def _enqueue(actor_name: str, run_id: Any) -> None:
    from hello_guy_ai import tasks

    try:
        getattr(tasks, actor_name).send(str(run_id))
    except Exception:  # broker down: fail visibly instead of leaving the thread locked
        logger.exception("Could not enqueue run %s", run_id)
        Run.objects.filter(pk=run_id, status=Run.Status.QUEUED).update(
            status=Run.Status.FAILED, error="Could not enqueue run", finished_at=timezone.now()
        )


def _fail_stale_runs(thread: Thread) -> None:
    """Unlock threads whose worker died mid-run (kill -9, OOM). Paused runs wait for a human."""
    cutoff = timezone.now() - timedelta(seconds=ai_settings.RUN_TIME_LIMIT_SECONDS + 60)
    stale = Run.objects.filter(thread=thread, status__in=Run.LIVE, last_active_at__lt=cutoff)
    if stale.update(status=Run.Status.FAILED, error="Run timed out (stale)", finished_at=timezone.now()):
        logger.warning("Marked stale runs failed on thread %s", thread.pk)


def _clean_text(value: Any, limit: int, label: str) -> str:
    text = str(value or "").strip()
    if not text or len(text) > limit:
        raise ValueError(f"{label} must be 1-{limit} characters")
    return text
