"""Staff-only AI Control Center views for LLM Models, Knowledge Bases, Agent Memory, and Triggers."""

from collections.abc import Callable
from functools import wraps

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db.models import Count, Q, Sum
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render

from hello_guy_ai.assistants import AIAssistant
from hello_guy_ai.models import AgentMemory, Document, KnowledgeBase, LLMModel, Run, Thread, Trigger


def _staff_view(fn: Callable[..., HttpResponse]) -> Callable[..., HttpResponse]:
    """Require an authenticated staff or superuser account."""

    @login_required
    @wraps(fn)
    def wrapper(request: HttpRequest, *args, **kwargs) -> HttpResponse:
        if not (request.user.is_staff or request.user.is_superuser):
            return render(request, "403.html", status=403)
        return fn(request, *args, **kwargs)

    return wrapper


def _validation_error_text(exc: ValidationError) -> str:
    if hasattr(exc, "message_dict"):
        return "; ".join(f"{field}: {', '.join(errs)}" for field, errs in exc.message_dict.items())
    return "; ".join(exc.messages)


@_staff_view
def ai_models(request: HttpRequest) -> HttpResponse:
    """List, add, activate, and delete Chat & Embedding LLMModel configurations."""
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "create":
            model = LLMModel(
                name=(request.POST.get("name") or "").strip(),
                kind=request.POST.get("kind") or LLMModel.Kind.CHAT,
                model_id=(request.POST.get("model_id") or "").strip(),
                api_base=(request.POST.get("api_base") or "").strip(),
                enabled=bool(request.POST.get("enabled")),
                is_default=bool(request.POST.get("is_default")),
            )
            raw_min = (request.POST.get("min_score") or "").strip()
            if raw_min:
                try:
                    model.min_score = float(raw_min)
                except ValueError:
                    messages.error(request, "Min similarity must be a number between 0 and 1.")
                    return redirect("hello_guy_ai_models")
            raw_key = (request.POST.get("api_key") or "").strip()
            if raw_key:
                model.set_api_key(raw_key)
            try:
                model.full_clean()
                model.save()
                messages.success(request, f"Saved LLM model '{model.name}'.")
            except ValidationError as exc:
                messages.error(request, _validation_error_text(exc))
            return redirect("hello_guy_ai_models")

        obj = get_object_or_404(LLMModel, pk=request.POST.get("id"))
        if action == "set-default":
            obj.is_default = True
            obj.enabled = True
            obj.save()
            messages.success(request, f"'{obj.name}' is now the default {obj.get_kind_display()} model.")
        elif action == "toggle-enabled":
            obj.enabled = not obj.enabled
            obj.save(update_fields=["enabled"])
            messages.success(request, f"'{obj.name}' {'enabled' if obj.enabled else 'disabled'}.")
        elif action == "delete":
            name = obj.name
            obj.delete()
            messages.success(request, f"Deleted LLM model '{name}'.")
        return redirect("hello_guy_ai_models")

    models_qs = list(LLMModel.objects.order_by("kind", "-is_default", "name"))
    return render(request, "hello_guy_ai/models.html", {
        "models": models_qs,
        "chat_models": [m for m in models_qs if m.kind == LLMModel.Kind.CHAT],
        "embed_models": [m for m in models_qs if m.kind == LLMModel.Kind.EMBEDDING],
        "active_chat": LLMModel.default(LLMModel.Kind.CHAT),
        "active_embed": LLMModel.default(LLMModel.Kind.EMBEDDING),
    })


@_staff_view
def ai_knowledge(request: HttpRequest) -> HttpResponse:
    """Manage RAG Knowledge Bases and Documents (upload, re-index, delete)."""
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "create-kb":
            kb = KnowledgeBase(
                slug=(request.POST.get("slug") or "").strip(),
                name=(request.POST.get("name") or "").strip(),
                description=(request.POST.get("description") or "").strip(),
                enabled=bool(request.POST.get("enabled")),
            )
            try:
                kb.full_clean()
                kb.save()
                messages.success(request, f"Created knowledge base '{kb.name}'.")
            except ValidationError as exc:
                messages.error(request, _validation_error_text(exc))
            return redirect("hello_guy_ai_knowledge")

        if action == "toggle-kb":
            kb = get_object_or_404(KnowledgeBase, pk=request.POST.get("id"))
            kb.enabled = not kb.enabled
            kb.save(update_fields=["enabled"])
            messages.success(request, f"Knowledge base '{kb.name}' {'enabled' if kb.enabled else 'paused'}.")
            return redirect("hello_guy_ai_knowledge")

        if action == "upload-doc":
            kb = get_object_or_404(KnowledgeBase, pk=request.POST.get("knowledge_base"))
            uploaded = request.FILES.get("file")
            title = (request.POST.get("title") or (uploaded.name if uploaded else "")).strip()
            doc = Document(
                knowledge_base=kb,
                title=title[:255],
                file=uploaded,
                uploaded_by=request.user,
            )
            try:
                doc.full_clean()
                doc.save()
                from hello_guy_ai.tasks.actors import ingest_document

                ingest_document.send(doc.pk)
                messages.success(request, f"Uploaded '{doc.title}' and queued for indexing.")
            except ValidationError as exc:
                messages.error(request, _validation_error_text(exc))
            except Exception as exc:
                messages.warning(request, f"Uploaded '{doc.title}', but indexing queue failed: {exc}")
            return redirect("hello_guy_ai_knowledge")

        doc = get_object_or_404(Document, pk=request.POST.get("id"))
        if action == "reindex-doc":
            doc.status = Document.Status.PENDING
            doc.error = ""
            doc.save(update_fields=["status", "error"])
            try:
                from hello_guy_ai.tasks.actors import ingest_document

                ingest_document.send(doc.pk)
                messages.success(request, f"Re-indexing queued for '{doc.title}'.")
            except Exception as exc:
                messages.error(request, f"Could not enqueue re-index: {exc}")
        elif action == "delete-doc":
            title = doc.title
            doc.delete()
            messages.success(request, f"Deleted document '{title}'.")
        return redirect("hello_guy_ai_knowledge")

    kbs = KnowledgeBase.objects.annotate(
        doc_count=Count("documents"),
        ready_count=Count("documents", filter=Q(documents__status=Document.Status.READY)),
        failed_count=Count("documents", filter=Q(documents__status=Document.Status.FAILED)),
        total_chunks=Sum("documents__chunk_count"),
    ).order_by("name")
    docs = Document.objects.select_related("knowledge_base", "uploaded_by").order_by("-updated_at")[:100]
    return render(request, "hello_guy_ai/knowledge.html", {
        "knowledge_bases": kbs,
        "documents": docs,
        "embed_model": LLMModel.default(LLMModel.Kind.EMBEDDING),
    })


@_staff_view
def ai_memory(request: HttpRequest) -> HttpResponse:
    """View, create, toggle, and delete durable AgentMemory facts."""
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "create":
            scope = request.POST.get("scope") or AgentMemory.Scope.AGENT
            content = (request.POST.get("content") or "").strip()
            if not content:
                messages.error(request, "Memory content is required.")
                return redirect("hello_guy_ai_memory")
            AgentMemory.objects.create(
                agent_key=(request.POST.get("agent_key") or "").strip(),
                scope=scope,
                user=request.user if scope == AgentMemory.Scope.USER else None,
                content=content,
                keywords=(request.POST.get("keywords") or "").strip()[:500],
                origin=AgentMemory.Origin.AGENT,
                is_active=True,
            )
            messages.success(request, "Added agent memory.")
            return redirect("hello_guy_ai_memory")

        mem = get_object_or_404(AgentMemory, pk=request.POST.get("id"))
        if action == "toggle":
            mem.is_active = not mem.is_active
            mem.save(update_fields=["is_active"])
            messages.success(request, f"Memory {'activated' if mem.is_active else 'paused'}.")
        elif action == "delete":
            mem.delete()
            messages.success(request, "Deleted memory.")
        return redirect("hello_guy_ai_memory")

    agent_filter = (request.GET.get("agent") or "").strip()
    origin_filter = (request.GET.get("origin") or "").strip()
    qs = AgentMemory.objects.select_related("user").order_by("-created_at")
    if agent_filter:
        qs = qs.filter(agent_key=agent_filter)
    if origin_filter:
        qs = qs.filter(origin=origin_filter)
    agents = [{"id": cls.id, "name": cls.name} for cls in AIAssistant.registry().values()]
    return render(request, "hello_guy_ai/memory.html", {
        "memories": qs[:100],
        "agents": agents,
        "agent_filter": agent_filter,
        "origin_filter": origin_filter,
    })


@_staff_view
def ai_triggers(request: HttpRequest) -> HttpResponse:
    """Manage event-driven and scheduled cron Triggers."""
    if request.method == "POST":
        action = request.POST.get("action", "")
        if action == "create":
            ct_id = (request.POST.get("content_type") or "").strip()
            run_as_id = request.POST.get("run_as") or request.user.pk
            trig = Trigger(
                name=(request.POST.get("name") or "").strip(),
                agent_id=(request.POST.get("agent_id") or "").strip(),
                event=request.POST.get("event") or Trigger.Event.SCHEDULED,
                content_type=ContentType.objects.filter(pk=ct_id).first() if ct_id else None,
                condition=(request.POST.get("condition") or "").strip(),
                prompt_template=(request.POST.get("prompt_template") or "").strip(),
                cron=(request.POST.get("cron") or "").strip(),
                run_as_id=run_as_id,
                auto_approve=bool(request.POST.get("auto_approve")),
                enabled=bool(request.POST.get("enabled")),
            )
            try:
                trig.full_clean()
                trig.save()
                messages.success(request, f"Created trigger '{trig.name}'.")
            except ValidationError as exc:
                messages.error(request, _validation_error_text(exc))
            return redirect("hello_guy_ai_triggers")

        trig = get_object_or_404(Trigger, pk=request.POST.get("id"))
        if action == "toggle":
            trig.enabled = not trig.enabled
            trig.save(update_fields=["enabled"])
            messages.success(request, f"Trigger '{trig.name}' {'enabled' if trig.enabled else 'paused'}.")
        elif action == "fire":
            try:
                from hello_guy_ai.tasks.actors import fire_trigger

                fire_trigger.send(trig.pk)
                messages.success(request, f"Queued trigger '{trig.name}' to run now.")
            except Exception as exc:
                messages.error(request, f"Could not enqueue trigger: {exc}")
        elif action == "delete":
            name = trig.name
            trig.delete()
            messages.success(request, f"Deleted trigger '{name}'.")
        return redirect("hello_guy_ai_triggers")

    triggers = Trigger.objects.select_related("content_type", "run_as").order_by("-enabled", "name")
    agents = [{"id": cls.id, "name": cls.name} for cls in AIAssistant.registry().values()]
    content_types = ContentType.objects.filter(app_label__in=["cms", "crm", "pms", "core", "meta_connector"]).order_by("app_label", "model")
    staff_users = get_user_model().objects.filter(is_active=True, is_staff=True).order_by("username")
    return render(request, "hello_guy_ai/triggers.html", {
        "triggers": triggers,
        "agents": agents,
        "content_types": content_types,
        "staff_users": staff_users,
        "events": Trigger.Event.choices,
    })


@_staff_view
def ai_threads(request: HttpRequest) -> HttpResponse:
    """Inspect all AI chat threads (internal, website, Meta, trigger) with filters and delete."""
    if request.method == "POST" and request.POST.get("action") == "delete":
        thread = get_object_or_404(Thread, pk=request.POST.get("id"))
        if thread.runs.filter(status__in=Run.ACTIVE).exists():
            messages.error(request, "Cannot delete a thread while a run is active or paused.")
        else:
            name = thread.name or str(thread.pk)
            thread.delete()
            messages.success(request, f"Deleted thread '{name}'.")
        return redirect("hello_guy_ai_threads")

    assistant_filter = (request.GET.get("assistant") or "").strip()
    source_filter = (request.GET.get("source") or "").strip()
    q = (request.GET.get("q") or "").strip()

    qs = Thread.objects.select_related("created_by", "handled_by").annotate(
        msg_count=Count("messages", distinct=True),
        run_count=Count("runs", distinct=True),
    ).order_by("-updated_at")
    if assistant_filter:
        qs = qs.filter(assistant_id=assistant_filter)
    if source_filter:
        qs = qs.filter(source=source_filter)
    if q:
        qs = qs.filter(Q(name__icontains=q) | Q(external_id__icontains=q))

    agents = [{"id": cls.id, "name": cls.name} for cls in AIAssistant.registry().values()]
    return render(request, "hello_guy_ai/threads.html", {
        "threads": qs[:100],
        "agents": agents,
        "assistant_filter": assistant_filter,
        "source_filter": source_filter,
        "q": q,
        "stats": {
            "total": Thread.objects.count(),
            "waiting": Thread.objects.filter(needs_human=True).count(),
            "live": Thread.objects.filter(human_mode=True).count(),
            "external": Thread.objects.exclude(external_id="").count(),
        },
    })


@_staff_view
def ai_thread_detail(request: HttpRequest, thread_id) -> HttpResponse:
    """Read-only inspection of one AI thread's messages and runs."""
    thread = get_object_or_404(Thread.objects.select_related("created_by", "handled_by"), pk=thread_id)
    msgs = thread.messages.select_related("run").order_by("id")
    runs = thread.runs.order_by("-created_at")[:25]
    return render(request, "hello_guy_ai/thread_detail.html", {
        "thread": thread,
        "msgs": msgs,
        "runs": runs,
    })
