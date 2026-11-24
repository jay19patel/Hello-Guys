"""Public website chat API: no login required, for frontend chat widgets."""

from uuid import UUID, uuid4

from ninja import Router
from ninja.pagination import paginate
from ninja.responses import Status
from ninja.throttling import AnonRateThrottle

from hello_guy_ai.services import chat
from hello_guy_ai.api.schemas import (
    ErrorOut,
    MessageOut,
    PublicAgentOut,
    PublicMessageIn,
    PublicThreadOut,
    RunAccepted,
)
from hello_guy_ai.assistants import AIAssistant
from hello_guy_ai.conf import ai_settings

router = Router(auth=None, tags=["public"])


@router.get("/agent", response=PublicAgentOut)
def public_agent(request):
    """Name, description and starter prompts for the widget's welcome screen."""
    chat.public_user()  # 404 when public chat is disabled
    cls = AIAssistant.get_cls(ai_settings.PUBLIC_AGENT)
    return {"name": cls.name, "description": cls.description, "suggestions": cls.suggestions}


@router.post("/threads", response={201: PublicThreadOut, 404: ErrorOut}, throttle=[AnonRateThrottle("10/m")])
def public_create_thread(request):
    """A new private thread. Its random id is the visitor's key: keep it (localStorage) to continue later.

    Never keyed by IP: visitors behind one NAT/office/VPN would share, and read, one conversation.
    """
    thread = chat.external_thread(
        chat.public_user(), ai_settings.PUBLIC_AGENT, f"{chat.WEB_PREFIX}{uuid4().hex}", "Website visitor"
    )
    return Status(201, {"thread_id": thread.pk})


@router.get("/threads/{thread_id}/messages", response=list[MessageOut])
@paginate
def public_messages(request, thread_id: UUID):
    return chat.chat_messages(chat.public_threads().get(pk=thread_id))


@router.post(
    "/threads/{thread_id}/messages",
    response={202: RunAccepted, 400: ErrorOut, 404: ErrorOut, 409: ErrorOut},
    throttle=[AnonRateThrottle(f"{ai_settings.PUBLIC_MESSAGES_PER_MINUTE}/m")],
)
def public_send(request, thread_id: UUID, payload: PublicMessageIn):
    thread = chat.public_threads().get(pk=thread_id)
    if thread.human_mode:  # a team member is chatting: their replies arrive on /events
        message = chat.post_live_message(thread, payload.content)
        return Status(202, {"message_id": message.pk, "mode": "human"})
    message, run = chat.post_message(thread.created_by, thread, payload.content)
    return Status(202, {"run_id": run.pk, "message_id": message.pk, "stream_url": chat.stream_url(run)})


@router.post("/threads/{thread_id}/handoff", response={202: dict, 404: ErrorOut}, throttle=[AnonRateThrottle("5/m")])
def public_handoff(request, thread_id: UUID):
    """The visitor asks to talk to a person ("Talk to the team" button)."""
    thread = chat.public_threads().get(pk=thread_id)
    if not (thread.human_mode or thread.needs_human):
        chat.request_human(thread, "Visitor asked for a person")
    return Status(202, {"status": "team notified"})
