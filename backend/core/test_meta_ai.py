from unittest.mock import patch

import pytest
from django.core.management import call_command

from hello_guy_ai.core import engine, prompt
from hello_guy_ai.tests.conftest import FakeLLM, broker, clear_cache, fake_redis, text_stream  # noqa: F401  fixtures
from hello_guy_ai.tests.test_engine import StubRag
from core import meta_ai as ai_reply
from hello_guy_ai.models import Thread
from hello_guy_ai.services import chat
from meta_connector.models import Contact, Message, PlatformAccount, WebhookLog
from meta_connector.services.webhook_processor import process_webhook_payload

pytestmark = pytest.mark.django_db
SEND = 'meta_connector.services.whatsapp_service.WhatsAppService.send_whatsapp_message'


def wa_payload(mid, text, phone="919999999999"):
    return {"object": "whatsapp_business_account", "entry": [{"id": "WABA", "changes": [{"field": "messages", "value": {
        "messaging_product": "whatsapp", "metadata": {"phone_number_id": "PNID"},
        "contacts": [{"profile": {"name": "Ravi"}, "wa_id": phone}],
        "messages": [{"from": phone, "id": mid, "timestamp": "1", "type": "text", "text": {"body": text}}],
    }}]}]}


@pytest.fixture
def account():
    call_command("setup_website_bot")
    return PlatformAccount.objects.create(platform='whatsapp', display_name='WA', phone_number_id='PNID', ai_auto_reply=True)


def receive(payload, capture):
    with capture(execute=True):
        process_webhook_payload(WebhookLog.objects.create(platform='whatsapp', payload=payload).id)


def test_one_thread_per_contact_and_replies_sent(account, broker, monkeypatch, django_capture_on_commit_callbacks):
    llm = FakeLLM(text_stream("Hi Ravi! We build AI chatbots."), text_stream("Sure, share your email."))
    monkeypatch.setattr(engine.litellm, "completion", llm)
    monkeypatch.setattr(prompt.litellm, "completion", llm)
    monkeypatch.setattr(ai_reply, "AgentRunner", lambda run: engine.AgentRunner(run, rag=StubRag()))

    sent = [(True, {"messages": [{"id": f"wamid.out{i}"}]}, "") for i in range(2)]
    with patch(SEND, side_effect=sent) as send:
        for mid, text in [("wamid.1", "Do you build chatbots?"), ("wamid.2", "I want a quote")]:
            receive(wa_payload(mid, text), django_capture_on_commit_callbacks)
            assert len(broker.queues[ai_reply.CHAT_QUEUE].queue) == 1  # queued for the worker
            broker.flush_all()
            ai_reply.reply_with_ai(Message.objects.get(message_id=mid).pk)  # what the worker runs

    contact = Contact.objects.get()
    thread = Thread.objects.get()  # one thread for the contact, both turns in it
    assert thread.external_id == "whatsapp:919999999999" and thread.runs.count() == 2
    assert [c.kwargs["text"] for c in send.call_args_list] == ["Hi Ravi! We build AI chatbots.", "Sure, share your email."]
    assert {m.sent_by for m in Message.objects.filter(direction='outgoing')} == {chat.public_user()}  # "AI assistant"
    history = [m["content"] for m in llm.calls[1]["messages"] if m["role"] in ("user", "assistant")]
    assert history[-3:] == ["Do you build chatbots?", "Hi Ravi! We build AI chatbots.", "I want a quote"]


def test_nothing_queued_when_auto_reply_off(account, broker, django_capture_on_commit_callbacks):
    PlatformAccount.objects.filter(pk=account.pk).update(ai_auto_reply=False)
    receive(wa_payload("wamid.3", "hello"), django_capture_on_commit_callbacks)
    assert not broker.queues.get(ai_reply.CHAT_QUEUE) or not broker.queues[ai_reply.CHAT_QUEUE].queue


def test_markdown_is_converted_for_each_platform():
    md = "## Hi\nWe are **NJ Tech Studio**. See [our work](https://nj.studio/work)."
    assert ai_reply.for_platform(md, 'whatsapp') == "Hi\nWe are *NJ Tech Studio*. See our work: https://nj.studio/work."
    assert ai_reply.for_platform(md, 'instagram') == "Hi\nWe are NJ Tech Studio. See our work: https://nj.studio/work."
    assert len(ai_reply.for_platform("x" * 5000, 'instagram')) == 1000
