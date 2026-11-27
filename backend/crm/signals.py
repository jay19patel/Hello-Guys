"""Live-chat alerts: hello_guy_ai says a chat needs a person; the CRM tells its team."""

from django.dispatch import receiver
from django.urls import reverse

from meta_connector.roles import notify_team
from hello_guy_ai.services.chat import human_requested, live_message_received


@receiver(human_requested, dispatch_uid="crm_human_requested")
def chat_needs_a_person(sender, thread, reason="", **kwargs):
    notify_team(f"Chat waiting: {thread.name or 'website visitor'}", reason or "Asked to talk to the team",
                reverse("crm:inbox-thread", args=[thread.pk]))


@receiver(live_message_received, dispatch_uid="crm_live_message")
def visitor_replied(sender, thread, message, **kwargs):
    from core import notifications

    url = reverse("crm:inbox-thread", args=[thread.pk])
    if thread.handled_by_id:
        notifications.notify(
            user=thread.handled_by,
            title=f"New message from {thread.name or 'visitor'}",
            body=message.content[:300],
            url=url,
            category="crm",
        )
    else:
        notify_team(f"New message from {thread.name or 'visitor'}", message.content[:300], url)
