"""REST API (Django Ninja). Mounted at /api/ai/. Session auth + CSRF."""

from uuid import UUID

from ninja import File, Form, NinjaAPI, UploadedFile
from ninja.pagination import paginate
from ninja.security import django_auth
from ninja.throttling import AuthRateThrottle

from hello_guy_ai import services
from hello_guy_ai.engine import public_questions
from hello_guy_ai.exceptions import AssistantNotFound, NotAllowed, RunConflict, ThreadBusy
from hello_guy_ai.models import Document, KnowledgeBase, Message, Run, Thread
from hello_guy_ai.schemas import (
    AssistantOut,
    DocumentOut,
    ErrorOut,
    FeedbackIn,
    FeedbackOut,
    KnowledgeBaseOut,
    MessageIn,
    MessageOut,
    ResumeIn,
    RunAccepted,
    RunOut,
    StopOut,
    ThreadDetailOut,
    ThreadIn,
    ThreadOut,
    ThreadPatch,
)

api = NinjaAPI(title="Hello Guy AI", version="2.0.0", urls_namespace="hello_guy_ai", auth=django_auth)
E404 = {404: ErrorOut}


@api.exception_handler(AssistantNotFound)
@api.exception_handler(Thread.DoesNotExist)
@api.exception_handler(Run.DoesNotExist)
@api.exception_handler(Document.DoesNotExist)
def _not_found(request, exc):
    return api.create_response(request, {"message": "Not found"}, status=404)


@api.exception_handler(NotAllowed)
def _forbidden(request, exc):
    return api.create_response(request, {"message": str(exc)}, status=403)


@api.exception_handler(ThreadBusy)
@api.exception_handler(RunConflict)
def _conflict(request, exc):
    return api.create_response(request, {"message": str(exc)}, status=409)


@api.exception_handler(ValueError)
def _bad_request(request, exc):
    return api.create_response(request, {"message": str(exc)}, status=400)


def _staff(request) -> None:
    if not request.user.is_staff:
        raise NotAllowed("Staff only")


# ---- assistants & threads ------------------------------------------------------


@api.get("/assistants", response=list[AssistantOut])
def list_assistants(request):
    return services.list_assistants(request.user)


@api.get("/threads", response=list[ThreadOut])
@paginate
def list_threads(request, assistant_id: str | None = None):
    return services.threads_for(request.user, assistant_id)


@api.post("/threads", response={201: ThreadOut, 403: ErrorOut, 404: ErrorOut})
def create_thread(request, payload: ThreadIn):
    return 201, services.create_thread(request.user, payload.assistant_id, payload.name)


@api.get("/threads/{thread_id}", response={200: ThreadDetailOut, **E404})
def get_thread(request, thread_id: UUID):
    thread = services.get_thread(request.user, thread_id)
    run = services.active_run(thread)
    thread.active_run = run and {
        "id": run.pk,
        "status": run.status,
        "stream_url": services.stream_url(run),
        "questions": public_questions(run.questions or []) if run.status == Run.Status.PAUSED else [],
    }
    return thread


@api.patch("/threads/{thread_id}", response={200: ThreadOut, **E404})
def rename_thread(request, thread_id: UUID, payload: ThreadPatch):
    thread = services.get_thread(request.user, thread_id)
    thread.name = payload.name
    thread.save(update_fields=["name", "updated_at"])
    return thread


@api.delete("/threads/{thread_id}", response={204: None, **E404})
def delete_thread(request, thread_id: UUID):
    services.get_thread(request.user, thread_id).delete()
    return 204, None


# ---- messages & runs ----------------------------------------------------------------


@api.get("/threads/{thread_id}/messages", response=list[MessageOut])
@paginate
def list_messages(request, thread_id: UUID, include_tools: bool = False):
    qs = services.get_thread(request.user, thread_id).messages.select_related("run")
    return qs if include_tools else qs.exclude(role=Message.Role.TOOL).exclude(content="")


@api.post(
    "/threads/{thread_id}/messages",
    response={202: RunAccepted, 400: ErrorOut, 403: ErrorOut, 404: ErrorOut, 409: ErrorOut},
    throttle=[AuthRateThrottle("30/m")],
)
def send_message(request, thread_id: UUID, payload: MessageIn):
    thread = services.get_thread(request.user, thread_id)
    message, run = services.post_message(request.user, thread, payload.content)
    return 202, {"run_id": run.pk, "message_id": message.pk, "stream_url": services.stream_url(run)}


@api.get("/runs/{run_id}", response={200: RunOut, **E404})
def get_run(request, run_id: UUID):
    return services.get_run(request.user, run_id)


@api.post("/runs/{run_id}/resume", response={202: RunAccepted, 400: ErrorOut, 404: ErrorOut, 409: ErrorOut})
def resume_run(request, run_id: UUID, payload: ResumeIn):
    run = services.resume_run(request.user, services.get_run(request.user, run_id), payload.answers)
    return 202, {"run_id": run.pk, "stream_url": services.stream_url(run)}


@api.post("/runs/{run_id}/stop", response={200: StopOut, **E404})
def stop_run(request, run_id: UUID):
    return {"status": services.stop_run(request.user, services.get_run(request.user, run_id))}


@api.post("/runs/{run_id}/feedback", response={200: FeedbackOut, 400: ErrorOut, 404: ErrorOut, 409: ErrorOut})
def feedback(request, run_id: UUID, payload: FeedbackIn):
    run = services.get_run(request.user, run_id)
    return services.submit_feedback(request.user, run, payload.rating, payload.comment)


# ---- knowledge (staff) ---------------------------------------------------------------


@api.get("/knowledge-bases", response=list[KnowledgeBaseOut])
def list_knowledge_bases(request):
    _staff(request)
    return KnowledgeBase.objects.order_by("name")


@api.get("/documents", response=list[DocumentOut])
@paginate
def list_documents(request, knowledge_base: str | None = None):
    _staff(request)
    qs = Document.objects.select_related("knowledge_base")
    return qs.filter(knowledge_base__slug=knowledge_base) if knowledge_base else qs


@api.post("/documents", response={202: DocumentOut, 400: ErrorOut, 403: ErrorOut, 404: ErrorOut})
def upload_document(request, knowledge_base: Form[str], file: File[UploadedFile], title: Form[str] = ""):
    return 202, services.create_document(request.user, knowledge_base, file, title)


@api.delete("/documents/{doc_id}", response={204: None, 403: ErrorOut, 404: ErrorOut})
def delete_document(request, doc_id: int):
    _staff(request)
    Document.objects.get(pk=doc_id).delete()
    return 204, None
