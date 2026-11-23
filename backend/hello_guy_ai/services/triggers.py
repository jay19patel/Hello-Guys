"""Triggers: run an agent when a model row is created/updated/deleted, or on a cron (Flow: flow.triggers).

Request path stays cheap: one cached index lookup per save; matching triggers are enqueued
on commit. Everything else (condition, prompt, agent run) happens in the worker.

Safety:
- Conditions use safe_expr (no eval); prompts see a dict of non-sensitive fields, not the instance.
- Recursion guard: writes made *by* a trigger run never fire triggers again.
- Runs as `run_as`, with that user's permissions; auto_approve is explicit per trigger.
"""

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import datetime
from decimal import Decimal
from typing import Any

from croniter import croniter
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import DatabaseError, models, transaction
from django.db.models.signals import post_delete, post_save
from django.template import Context, Engine, TemplateSyntaxError
from django.utils import timezone

from hello_guy_ai.core.runtime import SENSITIVE_FIELD_PATTERN as SENSITIVE
from hello_guy_ai.core.safe_expr import ExpressionError, compile_expression, evaluate
from hello_guy_ai.models import Trigger

logger = logging.getLogger(__name__)

INDEX_KEY = "hello_guy_ai:trigger-index"
IGNORED_APPS = {"hello_guy_ai", "sessions", "admin", "contenttypes", "django_dramatiq", "migrations"}
CONDITION_NAMES = {"obj", "event"}
MAX_PROMPT_CHARS = 20000
_PROMPT_ENGINE = Engine(autoescape=False)  # prompts are plain text, not HTML

_in_trigger_run: ContextVar[bool] = ContextVar("hello_guy_in_trigger_run", default=False)


@contextmanager
def trigger_run_scope() -> Iterator[None]:
    token = _in_trigger_run.set(True)
    try:
        yield
    finally:
        _in_trigger_run.reset(token)


# ---- request side: signals -> enqueue ------------------------------------------


def connect_signals() -> None:
    post_save.connect(_on_save, dispatch_uid="hello_guy_ai_trigger_save")
    post_delete.connect(_on_delete, dispatch_uid="hello_guy_ai_trigger_delete")
    post_save.connect(_invalidate, sender=Trigger, dispatch_uid="hello_guy_ai_trigger_index_save")
    post_delete.connect(_invalidate, sender=Trigger, dispatch_uid="hello_guy_ai_trigger_index_delete")


def _on_save(sender, instance, created=False, raw=False, **kwargs) -> None:
    _dispatch(sender, instance, Trigger.Event.CREATED if created else Trigger.Event.UPDATED, raw)


def _on_delete(sender, instance, **kwargs) -> None:
    _dispatch(sender, instance, Trigger.Event.DELETED)


def _dispatch(sender: type[models.Model], instance: models.Model, event: str, raw: bool = False) -> None:
    # __fake__ = historical models inside migrations; never dispatch from there.
    if raw or _in_trigger_run.get() or sender.__module__ == "__fake__" or sender._meta.app_label in IGNORED_APPS:
        return
    try:
        trigger_ids = _index().get(f"{sender._meta.label_lower}:{event}")  # no DB hit on the hot path
    except Exception:  # Redis/cache down: the user's save must never fail because of triggers
        logger.warning("Trigger index unavailable; skipping dispatch for %s", sender._meta.label_lower)
        return
    if not trigger_ids:
        return
    # Deleted rows are gone by the time the worker runs: ship their fields along.
    snapshot = serialize_instance(instance) if event == Trigger.Event.DELETED else None
    object_pk = str(instance.pk)
    from hello_guy_ai.tasks.actors import fire_trigger

    for trigger_id in trigger_ids:
        transaction.on_commit(lambda t=trigger_id: _send(fire_trigger, t, object_pk, snapshot))


def _send(actor, *args) -> None:
    try:
        actor.send(*args)
    except Exception:  # broker down after commit: log, don't 500 a request whose data is already saved
        logger.exception("Could not enqueue trigger %s", args[0])


def _index() -> dict[str, list[int]]:
    index = cache.get(INDEX_KEY)
    if index is None:
        try:
            with transaction.atomic():  # savepoint: a failure must not abort an outer (migration) transaction
                index = _build_index()
        except DatabaseError:  # table not migrated yet (saves during `migrate`)
            return {}
        cache.set(INDEX_KEY, index, 300)
    return index


def _build_index() -> dict[str, list[int]]:
    index: dict[str, list[int]] = {}
    rows = Trigger.objects.filter(enabled=True, content_type__isnull=False).exclude(event=Trigger.Event.SCHEDULED)
    for pk, app_label, model, event in rows.values_list(
        "pk", "content_type__app_label", "content_type__model", "event"
    ):
        index.setdefault(f"{app_label}.{model}:{event}", []).append(pk)
    return index


def _invalidate(**kwargs) -> None:
    cache.delete(INDEX_KEY)


# ---- worker side ------------------------------------------------------------------


def fire(trigger_id: int, object_pk: str | None = None, snapshot: dict | None = None) -> Any:
    """Evaluate condition, render prompt, run the agent synchronously. Returns the Run or None."""
    from hello_guy_ai.core.engine import AgentRunner
    from hello_guy_ai.services.chat import start_agent_run

    trigger = Trigger.objects.select_related("run_as", "content_type").filter(pk=trigger_id, enabled=True).first()
    if trigger is None:
        return None
    if not trigger.run_as.is_active:
        logger.warning("Trigger %s skipped: run_as user is inactive", trigger_id)
        return None
    data = snapshot or {}
    if object_pk and trigger.event in (Trigger.Event.CREATED, Trigger.Event.UPDATED):
        instance = trigger.content_type.model_class()._default_manager.filter(pk=object_pk).first()
        if instance is None:
            return None  # deleted before we got here
        data = serialize_instance(instance)
    if trigger.condition and not _passes(trigger, data):
        return None
    prompt = render_prompt(trigger.prompt_template, {"obj": data, "event": trigger.event, "now": timezone.localtime()})
    with trigger_run_scope():
        run = start_agent_run(
            trigger.run_as, trigger.agent_id, prompt, trigger=trigger, auto_approve=trigger.auto_approve
        )
        AgentRunner(run).execute()
    run.refresh_from_db()
    return run


def due_scheduled_triggers(now: datetime | None = None) -> list[int]:
    """Claim scheduled triggers that are due. Compare-and-set on last_fired_at = exactly once,
    even with several schedulers. A long outage fires once, not once per missed slot."""
    now = now or timezone.now()
    claimed: list[int] = []
    for trigger in Trigger.objects.filter(enabled=True, event=Trigger.Event.SCHEDULED):
        anchor = timezone.localtime(trigger.last_fired_at or trigger.created_at)  # cron in TIME_ZONE
        try:
            next_at = croniter(trigger.cron, anchor).get_next(datetime)
        except (ValueError, KeyError):
            logger.error("Trigger %s has an invalid cron %r", trigger.pk, trigger.cron)
            continue
        if next_at > now:
            continue
        won = Trigger.objects.filter(pk=trigger.pk, last_fired_at=trigger.last_fired_at).update(last_fired_at=now)
        if won:
            claimed.append(trigger.pk)
    return claimed


# ---- helpers -----------------------------------------------------------------------


def serialize_instance(instance: models.Model) -> dict:
    data = {
        f.attname: getattr(instance, f.attname) for f in instance._meta.concrete_fields if not SENSITIVE.search(f.name)
    }
    return json.loads(json.dumps(data, cls=_NumericEncoder))


class _NumericEncoder(DjangoJSONEncoder):
    """Decimals stay numbers so conditions like `obj.amount >= 1000` compare numerically."""

    def default(self, o: Any) -> Any:
        return float(o) if isinstance(o, Decimal) else super().default(o)


def render_prompt(template: str, context: dict) -> str:
    return _PROMPT_ENGINE.from_string(template).render(Context(context))[:MAX_PROMPT_CHARS].strip()


def _passes(trigger: Trigger, data: dict) -> bool:
    try:
        return evaluate(trigger.condition, {"obj": data, "event": trigger.event})
    except Exception:
        logger.exception("Trigger %s condition failed; treating as false", trigger.pk)
        return False


def validate_trigger(trigger: Trigger) -> None:
    from hello_guy_ai.services.agents import agent_exists

    errors: dict[str, str] = {}
    if trigger.agent_id and not agent_exists(trigger.agent_id):
        errors["agent_id"] = "No agent with this id (agents are defined in ai_assistants.py)."
    if trigger.event == Trigger.Event.SCHEDULED:
        if not trigger.cron or not croniter.is_valid(trigger.cron):
            errors["cron"] = "A valid cron expression is required for scheduled triggers."
    elif not trigger.content_type_id:
        errors["content_type"] = "Pick the model this trigger watches."
    elif trigger.content_type.app_label in IGNORED_APPS:
        errors["content_type"] = "This model cannot be watched."
    if trigger.condition:
        try:
            compile_expression(trigger.condition, CONDITION_NAMES)
        except ExpressionError as exc:
            errors["condition"] = str(exc)
    try:
        _PROMPT_ENGINE.from_string(trigger.prompt_template or "")
    except TemplateSyntaxError as exc:
        errors["prompt_template"] = f"Template error: {exc}"
    if trigger.run_as_id and not trigger.run_as.is_active:
        errors["run_as"] = "Must be an active user."
    if errors:
        raise ValidationError(errors)
