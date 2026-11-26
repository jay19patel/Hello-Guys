"""Tests for CRM Meetings, Google Calendar sync, RSVP/proposed-time workflows, and MeetingTranscript storage."""

from datetime import timedelta
from unittest.mock import patch

import pytest
from django.core.files.uploadedfile import SimpleUploadedFile
from django.urls import reverse
from django.utils import timezone

from crm import google, meetings as meetings_svc
from crm.models import Customer, GoogleAccount, Meeting, MeetingTranscript
from meta_connector.roles import ADMIN, VIEWER, set_role

pytestmark = pytest.mark.django_db


@pytest.fixture
def admin_client(client, django_user_model):
    user = django_user_model.objects.create_user("boss", "boss@example.com", "pass12345")
    set_role(user, ADMIN)
    client.force_login(user)
    return client


@pytest.fixture
def google_api(settings):
    settings.DEBUG = True
    account = GoogleAccount.get_solo()
    account.client_id = "cid"
    account.client_secret = "csecret"
    account.refresh_token = "rtoken"
    account.email = "team@studio.com"
    account.save()
    with patch.object(google, "build") as build:
        yield build.return_value


def test_meetings_hub_and_tabs_render(admin_client):
    customer = Customer.objects.create(name="Acme", email="client@acme.com")
    start = timezone.now() + timedelta(days=1)
    m = Meeting.objects.create(
        title="Product Demo",
        customer=customer,
        start_at=start,
        end_at=start + timedelta(hours=1),
        attendees=["client@acme.com", "cto@acme.com"],
        guest_responses={"client@acme.com": "accepted", "cto@acme.com": "tentative"},
        proposed_times=[{"id": "p1", "email": "cto@acme.com", "note": "Can we do 4 PM?", "status": "pending"}],
    )
    MeetingTranscript.objects.create(
        meeting=m,
        title="Demo Call Transcript",
        raw_text="Jay: Welcome!\nClient: Thanks for having us.",
        segments=[{"speaker": "Jay", "timestamp": "00:00", "text": "Welcome!"}],
    )

    for tab in ("upcoming", "proposals", "transcripts", "past", "cancelled", "all"):
        res = admin_client.get(reverse("crm:meetings") + f"?tab={tab}")
        assert res.status_code == 200
    assert admin_client.get(reverse("crm:meeting", args=[m.pk])).status_code == 200


def test_import_calendar_events_and_proposed_time_resolution(google_api, admin_client):
    Customer.objects.create(name="Acme", email="client@acme.com")
    start = (timezone.now() + timedelta(days=2)).replace(microsecond=0)
    end = start + timedelta(hours=1)
    events = google_api.events.return_value
    events.list.return_value.execute.return_value = {
        "items": [
            {
                "id": "gcal_1",
                "status": "confirmed",
                "summary": "Architecture Review",
                "htmlLink": "https://calendar.google.com/event?eid=1",
                "hangoutLink": "https://meet.google.com/xyz-abcd-efg",
                "start": {"dateTime": start.isoformat(), "timeZone": "Asia/Kolkata"},
                "end": {"dateTime": end.isoformat(), "timeZone": "Asia/Kolkata"},
                "attendees": [
                    {"email": "client@acme.com", "displayName": "Client Lead", "responseStatus": "accepted"},
                    {
                        "email": "cto@acme.com",
                        "displayName": "CTO",
                        "responseStatus": "tentative",
                        "comment": "Proposed new time: tomorrow 4 PM",
                    },
                    {"email": "ops@acme.com", "responseStatus": "declined"},
                ],
            }
        ]
    }

    res = admin_client.post(reverse("crm:meetings-sync"))
    assert res.status_code == 302

    m = Meeting.objects.get(google_event_id="gcal_1")
    assert m.customer.name == "Acme"
    assert m.rsvp_summary == {"total": 3, "accepted": 1, "declined": 1, "tentative": 1, "pending": 0}
    assert len(m.pending_proposals) == 1
    assert "Proposed new time" in m.pending_proposals[0]["note"]

    # Log a structured proposed time and accept it -> reschedules + patches Google Calendar
    new_start = start + timedelta(hours=3)
    new_end = end + timedelta(hours=3)
    proposal = meetings_svc.propose_meeting_time(
        m,
        email="cto@acme.com",
        name="CTO",
        proposed_start=new_start,
        proposed_end=new_end,
        note="3 hours later works best",
    )
    events.patch.return_value.execute.return_value = {"id": "gcal_1"}
    post_res = admin_client.post(
        reverse("crm:meeting", args=[m.pk]),
        {"action": "resolve-proposal", "proposal_id": proposal["id"], "decision": "accepted"},
    )
    assert post_res.status_code == 302
    m.refresh_from_db()
    assert m.start_at == new_start
    assert events.patch.called


def test_transcript_upload_parsing_and_api_storage(admin_client):
    customer = Customer.objects.create(name="Beta Corp")
    start = timezone.now()
    m = Meeting.objects.create(
        title="Kickoff",
        customer=customer,
        start_at=start,
        end_at=start + timedelta(minutes=45),
    )

    vtt = b"WEBVTT\n\n00:00:01.000 --> 00:00:04.000\n<v Jay>Hello team</v>\n\n00:00:05.000 --> 00:00:08.000\n<v Client>Hi Jay</v>\n"
    upload = SimpleUploadedFile("call.vtt", vtt, content_type="text/vtt")
    res = admin_client.post(
        reverse("crm:meeting", args=[m.pk]),
        {"action": "add-transcript", "title": "Kickoff VTT", "source": "upload", "transcript_file": upload},
    )
    assert res.status_code == 302
    tr = MeetingTranscript.objects.get(meeting=m, title="Kickoff VTT")
    assert tr.speaker_names == ["Jay", "Client"]
    assert len(tr.segments) == 2

    api_res = admin_client.post(
        reverse("crm:meeting-transcript-api", args=[m.pk]),
        data='{"raw_text": "Agent: Summary complete.\\nClient: Sounds good.", "source": "ai_agent", "summary": "Agreed on scope."}',
        content_type="application/json",
    )
    assert api_res.status_code == 201
    assert MeetingTranscript.objects.filter(meeting=m).count() == 2


def test_viewer_cannot_mutate_meetings(client, django_user_model):
    viewer = django_user_model.objects.create_user("viewer")
    set_role(viewer, VIEWER)
    client.force_login(viewer)
    start = timezone.now()
    m = Meeting.objects.create(title="Sync", start_at=start, end_at=start + timedelta(hours=1))
    assert client.get(reverse("crm:meetings")).status_code == 200
    assert client.get(reverse("crm:meeting", args=[m.pk])).status_code == 200
    assert client.post(reverse("crm:meetings-sync")).status_code == 403
    assert client.post(reverse("crm:meeting", args=[m.pk]), {"action": "cancel"}).status_code == 403
