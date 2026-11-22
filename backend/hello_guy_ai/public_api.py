"""Public website chat API: no login, for your frontend website's chat widget.

Every request acts as settings HELLO_GUY_AI["PUBLIC_USER"] and is pinned to PUBLIC_AGENT, so a visitor
gets exactly that agent's tools and that user's permissions (view public content + add inquiries).
The thread id (random UUID) is the visitor's key to their conversation: keep it in localStorage.
"""

from uuid import UUID

from ninja import Router, Schema
from ninja.pagination import paginate
from ninja.throttling import AnonRateThrottle
from pydantic import Field

from hello_guy_ai import services
from hello_guy_ai.assistant import AIAssistant
from hello_guy_ai.conf import ai_settings
from hello_guy_ai.schemas import ErrorOut, MessageOut, RunAccepted

router = Router(auth=None, tags=["public"])


class PublicAgentOut(Schema):
    name: str
    description: str
    suggestions: list[str]


class PublicThreadOut(Schema):
    thread_id: UUID


class PublicMessageIn(Schema):
    content: str = Field(min_length=1, max_length=2000)


@router.get("/agent", response=PublicAgentOut)
def public_agent(request):
    """Name, description and starter prompts for the widget's welcome screen."""
    services.public_user()  # 404 when public chat is disabled
    cls = AIAssistant.get_cls(ai_settings.PUBLIC_AGENT)
    return {"name": cls.name, "description": cls.description, "suggestions": cls.suggestions}


@router.post("/threads", response={201: PublicThreadOut, 404: ErrorOut}, throttle=[AnonRateThrottle("10/m")])
def public_create_thread(request):
    thread = services.create_thread(services.public_user(), ai_settings.PUBLIC_AGENT)
    return 201, {"thread_id": thread.pk}


@router.get("/threads/{thread_id}/messages", response=list[MessageOut])
@paginate
def public_messages(request, thread_id: UUID):
    return services.chat_messages(services.public_threads().get(pk=thread_id))


@router.post(
    "/threads/{thread_id}/messages",
    response={202: RunAccepted, 400: ErrorOut, 404: ErrorOut, 409: ErrorOut},
    throttle=[AnonRateThrottle(f"{ai_settings.PUBLIC_MESSAGES_PER_MINUTE}/m")],
)
def public_send(request, thread_id: UUID, payload: PublicMessageIn):
    thread = services.public_threads().get(pk=thread_id)
    message, run = services.post_message(thread.created_by, thread, payload.content)
    return 202, {"run_id": run.pk, "message_id": message.pk, "stream_url": services.stream_url(run)}
