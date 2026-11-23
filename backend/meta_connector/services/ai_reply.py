"""AI auto-reply for Instagram and WhatsApp DMs.

Each contact gets one hello_guy_ai thread (Contact.ai_thread), so the assistant remembers the conversation,
just like a visitor's thread in the website chat. Same assistant (PUBLIC_AGENT) and same user (PUBLIC_USER).
Turn it on per account: PlatformAccount.ai_auto_reply.
"""

import logging
import re

import dramatiq
from django.db import transaction

from hello_guy_ai.conf import ai_settings
from hello_guy_ai.core.engine import AgentRunner
from hello_guy_ai.exceptions import ThreadBusy
from hello_guy_ai.models import Message as AIMessage, Run
from hello_guy_ai.services import chat
from hello_guy_ai.tasks.actors import CHAT_QUEUE, RUN_LIMIT_MS  # importing it also installs the Redis broker
from meta_connector.models import Contact, Message, PlatformAccount
from meta_connector.services.message_service import MessageService

logger = logging.getLogger(__name__)

# Meta rejects longer texts: Instagram 1000 chars, WhatsApp 4096.
MAX_REPLY_CHARS = {PlatformAccount.PLATFORM_INSTAGRAM: 1000, PlatformAccount.PLATFORM_WHATSAPP: 4096}
BUSY_RETRIES = 10


def for_platform(text: str, platform: str) -> str:
    """LLM answers are Markdown; WhatsApp knows only *bold*/_italic_, Instagram shows plain text."""
    star = '*' if platform == PlatformAccount.PLATFORM_WHATSAPP else ''
    text = re.sub(r'\*\*(.+?)\*\*|__(.+?)__', lambda m: star + (m.group(1) or m.group(2)) + star, text)
    text = re.sub(r'\[([^\]]+)\]\((https?://[^)]+)\)', r'\1: \2', text)  # [title](url) -> title: url
    text = re.sub(r'^#{1,6}\s*', '', text, flags=re.M)  # headings
    return text.strip()[:MAX_REPLY_CHARS[platform]]


def queue_reply(message: Message) -> None:
    """Called by the webhook processor for each new incoming DM."""
    if (message.platform_account.ai_auto_reply and (message.message_text or '').strip()
            and not message.contact.is_blocked):
        transaction.on_commit(lambda: reply_with_ai.send(message.pk))


def _thread_for(contact: Contact, user):
    if contact.ai_thread_id:
        return contact.ai_thread
    label = contact.name or contact.username or contact.phone or contact.platform_user_id
    thread = chat.create_thread(user, ai_settings.PUBLIC_AGENT, name=f"{contact.platform_account.platform}: {label}"[:255])
    # Two DMs at once from a new contact: the first one to link its thread wins, the other is dropped.
    if not Contact.objects.filter(pk=contact.pk, ai_thread__isnull=True).update(ai_thread=thread):
        thread.delete()
        contact.refresh_from_db(fields=['ai_thread'])
        return contact.ai_thread
    return thread


# One run per thread: a DM that arrives while the previous answer is still running waits and retries.
# Nothing is retried after the run starts, so a reply is never sent twice.
@dramatiq.actor(
    queue_name=CHAT_QUEUE,
    time_limit=RUN_LIMIT_MS,
    min_backoff=3000,
    retry_when=lambda retries, exc: isinstance(exc, ThreadBusy) and retries < BUSY_RETRIES,
)
def reply_with_ai(message_id: int) -> None:
    msg = Message.objects.select_related('contact', 'platform_account', 'contact__ai_thread').filter(pk=message_id).first()
    if msg is None:
        return
    contact, account = msg.contact, msg.platform_account
    user = chat.public_user()
    thread = _thread_for(contact, user)
    _, run = chat.post_message(user, thread, msg.message_text, enqueue=False)
    AgentRunner(run).execute()
    run.refresh_from_db()

    answer = (AIMessage.objects.filter(run=run, role=AIMessage.Role.ASSISTANT).exclude(content='')
              .order_by('-id').values_list('content', flat=True).first())
    if run.status != Run.Status.SUCCEEDED or not answer:
        logger.warning("AI reply for %s message #%s not sent: run %s %s %s",
                       account.platform, msg.pk, run.pk, run.status, run.error)
        return
    _, ok, error = MessageService.send_message(
        account.platform, contact.platform_user_id, for_platform(answer, account.platform),
        account_id=account.pk, sent_by=user)  # sent_by = website-bot: the dashboard shows it as an AI reply
    if not ok:
        logger.warning("AI reply to %s contact #%s failed: %s", account.platform, contact.pk, error)

