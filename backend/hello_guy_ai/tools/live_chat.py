"""Handoff to a person (live chat). Add "request_human" to an assistant's builtin_tools to enable it."""

from hello_guy_ai.core.runtime import ToolError, current_context, tool


@tool
def request_human(reason: str = "") -> dict:
    """Ask a team member to join this chat. Use it when the person asks for a human, is upset, or needs
    something you cannot do or answer (pricing decisions, complaints, account-specific questions).
    After calling it, tell them a team member will reply here shortly."""
    from hello_guy_ai.models import Thread
    from hello_guy_ai.services import chat

    thread = Thread.objects.filter(pk=current_context().thread_id).first()
    if thread is None:
        raise ToolError("This conversation cannot be handed to a person.")
    if not thread.needs_human:
        chat.request_human(thread, reason)
    return {"status": "team notified"}
