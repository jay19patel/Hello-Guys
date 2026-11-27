"""AI auto-reply for WhatsApp / Instagram / Messenger DMs (turn it on per account: PlatformAccount.ai_auto_reply).

Each contact is one hello_guy_ai conversation ("<platform>:<account id>:<user id>"), so the assistant remembers
the chat. It stays quiet for non-text messages, once the chat is handed to the team, while a team member is
replying, and past an hourly limit. When it cannot answer, the team is notified.
Loaded by MetaConnectorConfig.ready() only when hello_guy_ai is installed.
"""

import logging
import re
from datetime import timedelta

import dramatiq
from django.conf import settings
from django.db import transaction
from django.db.models import Q
from django.dispatch import receiver
from django.urls import reverse
from django.utils import timezone

from hello_guy_ai import reply
from hello_guy_ai.exceptions import AssistantNotFound, ThreadBusy
from hello_guy_ai.models import Thread
from hello_guy_ai.services.chat import public_user
from hello_guy_ai.tasks.actors import CHAT_QUEUE, RUN_LIMIT_MS  # importing it also installs the Redis broker
from meta_connector.models import Contact, Message, PendingAIReply, PlatformAccount
from meta_connector.roles import notify_team
from meta_connector.services import SendError, send_message
from meta_connector.signals import message_received

logger = logging.getLogger(__name__)

# Meta rejects longer texts: Instagram/Messenger 1000 chars, WhatsApp 4096.
MAX_REPLY_CHARS = {PlatformAccount.PLATFORM_WHATSAPP: 4096}
BUSY_RETRIES = 10
# A team member's reply silences the AI for this long (they are handling the chat). 0 = never pause.
HUMAN_PAUSE = timedelta(minutes=getattr(settings, 'META_AI_HUMAN_PAUSE_MINUTES', 60))
# Cost/abuse cap: AI replies per contact per hour.
MAX_REPLIES_PER_HOUR = getattr(settings, 'META_AI_MAX_REPLIES_PER_HOUR', 10)


def thread_key(account: PlatformAccount, contact: Contact) -> str:
    """The contact's AI conversation. The account is part of it: two business numbers are two conversations."""
    return f"{account.platform}:{account.pk}:{contact.platform_user_id}"


def skip_reason(message: Message, bot) -> str | None:
    """Why this incoming DM must not get an AI reply (None = reply). Cheap checks, no LLM."""
    contact, now = message.contact, timezone.now()
    if not message.platform_account.ai_auto_reply:
        return "auto-reply off"
    if message.message_type != Message.TYPE_TEXT or not (message.message_text or '').strip():
        return "not a text message"  # reactions, locations, media: nothing to answer
    if contact.is_blocked:
        return "contact blocked"
    if Thread.objects.filter(Q(human_mode=True) | Q(needs_human=True),
                             external_id=thread_key(message.platform_account, contact)).exists():
        return "handed to the team"
    outgoing = Message.objects.filter(contact=contact, direction=Message.DIRECTION_OUTGOING)
    if HUMAN_PAUSE and outgoing.filter(created_at__gte=now - HUMAN_PAUSE, sent_by__isnull=False).exclude(
            sent_by=bot).exists():
        return "a team member is handling this chat"
    if outgoing.filter(sent_by=bot, created_at__gte=now - timedelta(hours=1)).count() >= MAX_REPLIES_PER_HOUR:
        return "hourly AI reply limit reached"
    return None


@receiver(message_received, dispatch_uid="meta_ai_reply")
def queue_reply(sender, message: Message, **kwargs) -> None:
    if not message.platform_account.ai_auto_reply:
        return
    try:
        bot = public_user()
    except AssistantNotFound:  # website bot not set up: nothing can reply
        return
    if reason := skip_reason(message, bot):
        logger.info("No AI reply for message #%s: %s", message.pk, reason)
    else:
        transaction.on_commit(lambda: reply_with_ai.send(message.pk))


def for_platform(text: str, platform: str) -> str:
    """LLM answers are Markdown; WhatsApp knows only *bold*/_italic_, Instagram shows plain text."""
    star = '*' if platform == PlatformAccount.PLATFORM_WHATSAPP else ''
    text = re.sub(r'\*\*(.+?)\*\*|__(.+?)__', lambda m: star + (m.group(1) or m.group(2)) + star, text)
    text = re.sub(r'\[([^\]]+)\]\((https?://[^)]+)\)', r'\1: \2', text)  # [title](url) -> title: url
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.M)  # headings
    return text.strip()[:MAX_REPLY_CHARS.get(platform, 1000)]


# One run per conversation: a DM that arrives while the previous answer is still running waits and retries.
# Nothing is retried after the run starts, so a reply is never sent twice.
@dramatiq.actor(
    queue_name=CHAT_QUEUE,
    time_limit=RUN_LIMIT_MS,
    min_backoff=3000,
    retry_when=lambda retries, exc: isinstance(exc, ThreadBusy) and retries < BUSY_RETRIES,
)
def reply_with_ai(message_id: int) -> None:
    msg = Message.objects.select_related('contact', 'platform_account').filter(pk=message_id).first()
    if msg is None:
        return
    contact, account, bot = msg.contact, msg.platform_account, public_user()
    if reason := skip_reason(msg, bot):  # re-check: a team member may have replied while this waited in the queue
        logger.info("No AI reply for message #%s: %s", msg.pk, reason)
        return
    label = contact.name or contact.username or contact.phone or contact.platform_user_id
    url = reverse('dashboard-contact', args=[contact.pk])
    from hello_guy_ai.conf import ai_settings
    from hello_guy_ai.services import chat as ai_chat

    if ai_chat.wants_human_handoff(msg.message_text):
        thread = ai_chat.external_thread(bot, ai_settings.PUBLIC_AGENT, thread_key(account, contact), f"{account.platform}: {label}")
        ai_chat.post_live_message(thread, msg.message_text)
        ai_chat.maybe_request_human(thread, msg.message_text)
        return
    answer = reply(thread_key(account, contact), msg.message_text, f"{account.platform}: {label}")
    if not answer:
        notify_team(f"AI could not answer {label}", f"Their message: {msg.message_text[:300]}", url)
        return
    formatted = for_platform(answer, account.platform)
    if account.ai_require_approval:
        PendingAIReply.objects.create(
            platform_account=account,
            contact=contact,
            trigger_message=msg,
            draft_text=formatted,
            reason="Account requires human approval before sending",
        )
        notify_team(f"AI reply awaiting approval: {label}", formatted[:300], reverse('dashboard-approvals'))
        return
    try:  # sent_by=bot: shows as "AI assistant" in the dashboard
        send_message(contact, formatted, sent_by=bot)
    except SendError as exc:
        logger.warning("AI reply to %s contact #%s failed: %s", account.platform, contact.pk, exc)
        notify_team(f"AI reply to {label} was not delivered", str(exc)[:300], url)
