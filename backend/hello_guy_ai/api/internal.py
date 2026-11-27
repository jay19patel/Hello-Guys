"""Authenticated internal REST API endpoints (mounted under /api/ai/)."""

from uuid import UUID

from ninja import Router
from ninja.pagination import paginate
from ninja.responses import Status
from ninja.throttling import AuthRateThrottle

from hello_guy_ai.access import chat_auth
from hello_guy_ai.services import chat
from hello_guy_ai.api.schemas import (
    AssistantOut,
    ErrorOut,
    FeedbackIn,
    FeedbackOut,
    MessageIn,
    MessageOut,
    ResumeIn,
    RunAccepted,
    StopOut,
    ThreadDetailOut,
    ThreadIn,
    ThreadOut,
    ThreadRenameIn,
)
from hello_guy_ai.core.engine import public_questions
from hello_guy_ai.models import Run

router = Router(auth=chat_auth, tags=["internal"])
E404 = {404: ErrorOut}


# ---- assistants & threads ------------------------------------------------------


@router.get("/assistants", response=list[AssistantOut])
def list_assistants(request):
    return chat.list_assistants(request.user)


@router.get("/threads", response=list[ThreadOut])
@paginate
def list_threads(request, assistant_id: str | None = None, q: str = ""):
    return chat.threads_for(request.user, assistant_id, query=q[:100])


@router.post("/threads", response={201: ThreadOut, 403: ErrorOut, 404: ErrorOut})
def create_thread(request, payload: ThreadIn):
    thread = chat.create_thread(request.user, payload.assistant_id, payload.name)
    return Status(201, thread)


@router.get("/threads/{thread_id}", response={200: ThreadDetailOut, **E404})
def get_thread(request, thread_id: UUID):
    thread = chat.get_thread(request.user, thread_id)
    run = chat.active_run(thread)
    thread.active_run = run and {
        "id": run.pk,
        "status": run.status,
        "stream_url": chat.stream_url(run),
        "questions": public_questions(run.questions or []) if run.status == Run.Status.PAUSED else [],
    }
    return thread


@router.patch("/threads/{thread_id}", response={200: ThreadOut, 400: ErrorOut, **E404})
def rename_thread(request, thread_id: UUID, payload: ThreadRenameIn):
    return chat.rename_thread(chat.get_thread(request.user, thread_id), payload.name)


@router.delete("/threads/{thread_id}", response={204: None, 409: ErrorOut, **E404})
def delete_thread(request, thread_id: UUID):
    chat.delete_thread(chat.get_thread(request.user, thread_id))
    return Status(204, None)


# ---- messages & runs ----------------------------------------------------------------


@router.get("/threads/{thread_id}/messages", response=list[MessageOut])
@paginate
def list_messages(request, thread_id: UUID):
    return chat.chat_messages(chat.get_thread(request.user, thread_id))


@router.post(
    "/threads/{thread_id}/messages",
    response={202: RunAccepted, 400: ErrorOut, 403: ErrorOut, 404: ErrorOut, 409: ErrorOut},
    throttle=[AuthRateThrottle("30/m")],
)
def send_message(request, thread_id: UUID, payload: MessageIn):
    thread = chat.get_thread(request.user, thread_id)
    message, run = chat.post_message(request.user, thread, payload.content, payload.timezone)
    return Status(202, {"run_id": run.pk, "message_id": message.pk, "stream_url": chat.stream_url(run)})


@router.post("/runs/{run_id}/resume", response={202: RunAccepted, 400: ErrorOut, 404: ErrorOut, 409: ErrorOut})
def resume_run(request, run_id: UUID, payload: ResumeIn):
    run = chat.resume_run(request.user, chat.get_run(request.user, run_id), payload.answers)
    return Status(202, {"run_id": run.pk, "stream_url": chat.stream_url(run)})


@router.post("/runs/{run_id}/stop", response={200: StopOut, **E404})
def stop_run(request, run_id: UUID):
    return {"status": chat.stop_run(request.user, chat.get_run(request.user, run_id))}


@router.post("/runs/{run_id}/feedback", response={200: FeedbackOut, 400: ErrorOut, 404: ErrorOut, 409: ErrorOut})
def feedback(request, run_id: UUID, payload: FeedbackIn):
    run = chat.get_run(request.user, run_id)
    return chat.submit_feedback(request.user, run, payload.rating, payload.comment)
