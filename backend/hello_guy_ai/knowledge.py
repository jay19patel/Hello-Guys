"""Sync database records into knowledge bases (RAG). Host apps declare sources in <app>/ai_knowledge.py:

    class BlogPostSource(KnowledgeSource):
        model = "cms.blogpost"            # app_label.model: renaming the app only changes this line
        knowledge_base = "company-public"  # KB slug, created on first sync

        def get_queryset(self):            # only rows the assistant may know (published, public)
            return BlogPost.objects.filter(is_published=True)

        def get_text(self, obj):
            return obj.content

Each record becomes one Document (linked by ContentType + object id) and reuses the normal ingest pipeline.
Save/delete signals sync one record, and `related` child models (e.g. a project's sections) re-sync their
parent. QuerySet.update()/bulk_create() send no signals: those changes are picked up by `reconcile()` (daily, from the scheduler), which also removes
records that left the queryset and orphans of sources that no longer exist (e.g. a renamed app).
Unchanged text (content hash) is never re-embedded.
"""

import hashlib
import logging
from typing import Any, ClassVar

from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured
from django.db import models, transaction
from django.db.models.signals import post_delete, post_save

from hello_guy_ai.models import Document, KnowledgeBase

logger = logging.getLogger(__name__)

SOURCES: dict[str, "KnowledgeSource"] = {}


class KnowledgeSource:
    model: ClassVar[str]
    knowledge_base: ClassVar[str]
    # Child models whose changes re-index the parent: {"app.childmodel": "<fk attname to this model>"},
    # e.g. {"cms.projectsection": "project_id"} when get_text() includes the sections.
    related: ClassVar[dict[str, str]] = {}
    # Shortcut instead of get_text(): model field names, written as "Verbose name: value" (empty ones skipped).
    fields: ClassVar[list[str]] = []

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        if not getattr(cls, "model", None):  # a shared base class, not a source
            return
        label = cls.model.lower()
        if label in SOURCES:  # one Document per record: a second source would silently replace the first
            raise ImproperlyConfigured(f"{cls.__name__}: {label} already has {type(SOURCES[label]).__name__}")
        SOURCES[label] = cls()

    def get_queryset(self) -> models.QuerySet:
        return apps.get_model(self.model)._default_manager.all()

    def get_title(self, obj: models.Model) -> str:
        return str(obj)

    def get_text(self, obj: models.Model) -> str:
        if not self.fields:
            raise NotImplementedError(f"{type(self).__name__}: set `fields` or override get_text()")
        lines = []
        for name in self.fields:
            value = getattr(obj, name)
            if isinstance(value, (list, tuple)):
                value = ", ".join(map(str, value))
            if value not in (None, ""):
                label = obj._meta.get_field(name).verbose_name
                lines.append(f"{label[:1].upper()}{label[1:]}: {value}")
        return "\n".join(lines)

    def get_url(self, obj: models.Model) -> str:
        return ""


def build_content(source: KnowledgeSource, obj: models.Model) -> tuple[str, str]:
    """Title + text. A short header on every record helps retrieval and lets the bot cite a link."""
    title = source.get_title(obj)[:255]
    header = f"{obj._meta.verbose_name.title()}: {title}"
    if url := source.get_url(obj):
        header += f"\nURL: {url}"
    return title, f"{header}\n\n{source.get_text(obj).strip()}"


def sync_object(model_label: str, object_pk: str) -> None:
    """Index, re-index or remove one record. Runs in the worker."""
    source = SOURCES.get(model_label)
    ct = ContentType.objects.get_for_model(apps.get_model(model_label))
    doc = Document.objects.filter(content_type=ct, object_id=str(object_pk)).first()
    obj = source.get_queryset().filter(pk=object_pk).first() if source else None
    if obj is None:  # deleted, unpublished, or no longer a source
        if doc:
            doc.delete()  # post_delete removes its vectors
        return
    _sync(source, ct, obj, doc)


def reconcile() -> None:
    """Full pass over every source. Safe to run any time."""
    kept_types = []
    for label, source in SOURCES.items():
        try:
            ct = ContentType.objects.get_for_model(apps.get_model(label))
        except LookupError:
            logger.warning("Knowledge source %s: model not installed", label)
            continue
        kept_types.append(ct.pk)
        docs = {d.object_id: d for d in Document.objects.filter(content_type=ct)}
        seen = set()
        for obj in source.get_queryset().iterator(chunk_size=500):  # chunk_size: works with prefetch_related too
            seen.add(str(obj.pk))
            _sync(source, ct, obj, docs.get(str(obj.pk)))
        for object_id, doc in docs.items():
            if object_id not in seen:
                doc.delete()
    # Records of sources that were removed or renamed.
    for doc in Document.objects.filter(content_type__isnull=False).exclude(content_type__in=kept_types):
        doc.delete()


def _sync(source: KnowledgeSource, ct: ContentType, obj: models.Model, doc: Document | None) -> None:
    from hello_guy_ai.services.chat import enqueue_ingest

    title, text = build_content(source, obj)
    digest = hashlib.sha256(f"{source.knowledge_base}\n{text}".encode()).hexdigest()
    # Same text already indexed or on its way (several saves in one admin form queue several jobs): skip.
    if doc and doc.content_hash == digest and doc.status != Document.Status.FAILED:
        return
    kb, _ = KnowledgeBase.objects.get_or_create(slug=source.knowledge_base, defaults={"name": source.knowledge_base})
    doc, _ = Document.objects.update_or_create(
        content_type=ct,
        object_id=str(obj.pk),
        defaults={
            "knowledge_base": kb,
            "title": title,
            "content": text,
            "content_hash": digest,
            "status": Document.Status.PENDING,
        },
    )
    enqueue_ingest(doc.pk)


# ---- signals --------------------------------------------------------------------


def connect_signals() -> None:
    for label, source in SOURCES.items():
        _watch(label, label, "pk")
        for child_label, fk in source.related.items():
            _watch(child_label, label, fk)


def _watch(sender_label: str, source_label: str, pk_attr: str) -> None:
    """On save/delete of `sender_label`, re-sync record `getattr(instance, pk_attr)` of `source_label`."""
    try:
        sender = apps.get_model(sender_label)
    except LookupError:
        logger.warning("Knowledge source %s: model %s not installed; skipped", source_label, sender_label)
        return

    def on_change(sender, instance: models.Model, raw: bool = False, **kwargs: Any) -> None:
        pk = getattr(instance, pk_attr, None)
        if raw or pk is None:  # loaddata, or a child not linked to a parent
            return
        transaction.on_commit(lambda: _send(source_label, str(pk)))

    uid = f"hello_guy_knowledge_{sender_label}_to_{source_label}"
    # weak=False: the handler is a closure, a weak reference would let it be garbage-collected.
    post_save.connect(on_change, sender=sender, weak=False, dispatch_uid=f"{uid}_save")
    post_delete.connect(on_change, sender=sender, weak=False, dispatch_uid=f"{uid}_delete")


def _send(label: str, pk: str) -> None:
    from hello_guy_ai.tasks.actors import sync_knowledge_record

    try:
        sync_knowledge_record.send(label, pk)
    except Exception:  # broker down: the daily reconcile catches up; never fail the user's save
        logger.exception("Could not enqueue knowledge sync for %s %s", label, pk)
