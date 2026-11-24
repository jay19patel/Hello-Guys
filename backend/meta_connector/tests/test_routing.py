"""Inbound events go to the account they belong to, or nowhere (never to some other account)."""

import pytest

from meta_connector.models import Message, PlatformAccount, WebhookLog
from meta_connector.services import webhook_processor

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def no_graph_calls(monkeypatch):
    monkeypatch.setattr(webhook_processor.ProfileService, "enrich_contact_profile", lambda *a, **k: None)


def dm(obj: str, entry_id: str, mid: str) -> None:
    payload = {"object": obj, "entry": [{"id": entry_id, "messaging": [
        {"sender": {"id": "user-1"}, "recipient": {"id": entry_id}, "message": {"mid": mid, "text": "hi"}}]}]}
    webhook_processor.process_webhook_payload(WebhookLog.objects.create(platform=obj, payload=payload).pk)


def test_messenger_goes_to_its_page_and_unknown_ids_are_skipped():
    ig = PlatformAccount.objects.create(platform="instagram", display_name="IG", instagram_id="IG1")
    fb = PlatformAccount.objects.create(platform="facebook", display_name="FB", instagram_id="PAGE1")
    dm("page", "PAGE1", "m1")
    dm("instagram", "IG-UNKNOWN", "m2")
    assert Message.objects.get(message_id="m1").platform_account == fb
    assert not Message.objects.filter(message_id="m2").exists()
    ig.refresh_from_db()
    assert ig.instagram_id == "IG1" and PlatformAccount.objects.count() == 2  # nothing overwritten or auto-created


def test_new_account_adopts_its_id_from_the_first_event():
    acc = PlatformAccount.objects.create(platform="instagram", display_name="IG")
    dm("instagram", "IG9", "m3")
    acc.refresh_from_db()
    assert acc.instagram_id == "IG9" and Message.objects.get(message_id="m3").platform_account == acc
