"""Google Calendar/Meet, Drive and OAuth, with Google's HTTP API mocked."""

from datetime import timedelta
from unittest.mock import MagicMock, patch
from urllib.parse import parse_qs, urlsplit

import pytest
from django.core import signing
from django.utils import timezone
from googleapiclient.errors import HttpError

from crm import google
from crm.models import Customer, GoogleAccount, GoogleFile, Meeting
from meta_connector.roles import ADMIN, set_role

pytestmark = pytest.mark.django_db


@pytest.fixture
def api():
    """Google's API client (googleapiclient.discovery.build) replaced by a mock."""
    with patch.object(google, "build") as build:
        yield build.return_value


@pytest.fixture
def connected(api):
    account = GoogleAccount.get_solo()
    account.client_id = "cid"
    account.client_secret = "csecret"
    account.refresh_token = "rtoken"
    account.save()
    return account


def test_secrets_are_encrypted_at_rest():
    account = GoogleAccount.get_solo()
    account.client_secret = "s3cret"
    account.refresh_token = "r3fresh"
    account.save()
    raw = GoogleAccount.objects.values("client_secret_encrypted", "refresh_token_encrypted").get()
    assert "s3cret" not in str(raw) and "r3fresh" not in str(raw)
    assert GoogleAccount.get_solo().client_secret == "s3cret"


def meeting(**fields):
    start = timezone.now() + timedelta(days=1)
    return Meeting.objects.create(title="Kickoff", customer=Customer.objects.create(name="Acme"), start_at=start,
                                  end_at=start + timedelta(hours=1), **fields)


def test_meeting_creates_calendar_event_with_meet_link(connected, api):
    m = meeting(attendees=["client@acme.com"])
    events = api.events.return_value
    events.insert.return_value.execute.return_value = {
        "id": "ev1", "htmlLink": "https://calendar.google.com/ev1", "hangoutLink": "https://meet.google.com/abc"}
    google.sync_meeting(m)
    kwargs = events.insert.call_args.kwargs
    assert (kwargs["calendarId"], kwargs["conferenceDataVersion"], kwargs["sendUpdates"]) == ("primary", 1, "all")
    assert kwargs["body"]["conferenceData"]["createRequest"]["conferenceSolutionKey"]["type"] == "hangoutsMeet"
    assert kwargs["body"]["attendees"] == [{"email": "client@acme.com"}]
    m.refresh_from_db()
    assert (m.google_event_id, m.meet_url, m.sync_error) == ("ev1", "https://meet.google.com/abc", "")

    m.status = Meeting.Status.CANCELLED
    google.sync_meeting(m)
    assert events.delete.call_args.kwargs == {"calendarId": "primary", "eventId": "ev1", "sendUpdates": "all"}


def test_google_failure_keeps_the_crm_meeting_and_records_the_error(connected, api):
    m = meeting()
    error = HttpError(MagicMock(status=403, reason="Forbidden"), b'{"error": {"message": "Calendar API off"}}')
    api.events.return_value.insert.return_value.execute.side_effect = error
    google.sync_meeting(m)
    m.refresh_from_db()
    assert m.pk and m.sync_error == "Calendar API off" and not m.google_event_id


def test_not_connected_meeting_is_crm_only():
    google.sync_meeting(meeting())
    assert "not connected" in Meeting.objects.get().sync_error


def test_drive_folder_once_then_sheet_and_doc_inside_and_share(connected, api, user):
    customer = Customer.objects.create(name="Acme", company="Acme Ltd")
    files = api.files.return_value
    files.create.return_value.execute.side_effect = [
        {"id": "root", "webViewLink": "u0"}, {"id": "cust", "webViewLink": "u1"},
        {"id": "sheet1", "name": "Budget", "webViewLink": "u2"}, {"id": "doc1", "name": "Notes"},
    ]
    gfile = google.create_file(customer, GoogleFile.Kind.SPREADSHEET, "Budget", user)
    google.create_file(customer, GoogleFile.Kind.DOCUMENT, "Notes", user)  # folders exist now: one create call
    google.share_file(gfile, "client@acme.com", "writer")
    bodies = [c.kwargs["body"] for c in files.create.call_args_list]
    assert bodies[0]["name"] == "CRM Customers" and bodies[1]["parents"] == ["root"]
    assert bodies[2] == {"name": "Budget", "mimeType": "application/vnd.google-apps.spreadsheet", "parents": ["cust"]}
    assert len(bodies) == 4 and bodies[3]["parents"] == ["cust"]
    permission = api.permissions.return_value.create.call_args.kwargs
    assert permission["fileId"] == "sheet1"
    assert permission["body"] == {"type": "user", "role": "writer", "emailAddress": "client@acme.com"}
    customer.refresh_from_db()
    gfile.refresh_from_db()
    assert customer.drive_folder_id == "cust" and gfile.shared_with[0]["email"] == "client@acme.com"
    with pytest.raises(google.GoogleError):
        google.share("x", "a@b.com", "owner")  # never hand out ownership


def test_oauth_callback_rejects_a_forged_state(client, django_user_model):
    admin = django_user_model.objects.create_user("boss")
    set_role(admin, ADMIN)
    client.force_login(admin)
    forged = signing.dumps({"nonce": "guess", "user": admin.pk}, salt="crm.google.oauth")
    assert client.get("/crm/google/callback/", {"state": forged, "code": "c"}).status_code == 404


def test_oauth_connect_and_callback_store_the_account(client, django_user_model, api):
    admin = django_user_model.objects.create_user("boss")
    set_role(admin, ADMIN)
    client.force_login(admin)
    account = GoogleAccount.get_solo()
    account.client_id, account.client_secret = "cid", "csecret"
    account.save()
    res = client.post("/crm/google/connect/")
    query = parse_qs(urlsplit(res.url).query)
    assert res.status_code == 302 and res.url.startswith(google.AUTH_URL)
    assert query["code_challenge_method"] == ["S256"] and query["access_type"] == ["offline"]

    def fetch_token(flow, code):
        assert code == "abc" and flow.code_verifier  # PKCE verifier carried over in the session
        flow.oauth2session.token = {"access_token": "at", "refresh_token": "rt", "token_type": "Bearer",
                                    "expires_at": 9999999999, "scope": google.SCOPES}

    api.userinfo.return_value.get.return_value.execute.return_value = {"email": "me@studio.com"}
    with patch.object(google.Flow, "fetch_token", autospec=True, side_effect=fetch_token):
        res = client.get("/crm/google/callback/", {"state": query["state"][0], "code": "abc"})
    account.refresh_from_db()
    assert res.status_code == 302 and account.email == "me@studio.com" and account.refresh_token == "rt"


def test_pull_meeting_from_google_reschedule_rename_and_rsvp(connected, api):
    m = meeting(google_event_id="ev1", attendees=["Client@Acme.com"])
    new_start = (m.start_at + timedelta(days=2)).replace(microsecond=0)
    api.events.return_value.get.return_value.execute.return_value = {
        "id": "ev1", "status": "confirmed", "updated": (timezone.now() + timedelta(minutes=1)).isoformat(),
        "summary": "Kickoff v2", "start": {"dateTime": new_start.isoformat(), "timeZone": "Asia/Kolkata"},
        "end": {"dateTime": (new_start + timedelta(hours=1)).isoformat()},
        "attendees": [{"email": "me@x.com", "self": True}, {"email": "client@acme.com", "responseStatus": "declined"}],
    }
    assert google.pull_meetings() == 1
    m.refresh_from_db()
    assert (m.title, m.start_at, m.guests) == ("Kickoff v2", new_start, [("client@acme.com", "declined")])
    note = m.customer.activities.get().body
    assert "rescheduled" in note and "client@acme.com declined" in note
    assert google.pull_meetings() == 0  # nothing new in Google: no second note
    assert m.customer.activities.count() == 1


def test_pull_meeting_keeps_a_newer_crm_edit_and_marks_cancelled(connected, api):
    m = meeting(google_event_id="ev1")
    get = api.events.return_value.get.return_value.execute
    get.return_value = {"status": "confirmed", "updated": (timezone.now() - timedelta(hours=1)).isoformat(),
                        "summary": "Old title", "start": {}, "end": {}}
    assert google.pull_meetings() == 0 and Meeting.objects.get().title == "Kickoff"
    get.return_value = {"status": "cancelled"}
    assert google.pull_meetings() == 1 and Meeting.objects.get().status == Meeting.Status.CANCELLED


def test_crm_edit_sends_known_rsvp_back_to_google(connected, api):
    m = meeting(google_event_id="ev1", attendees=["a@b.com"], guest_responses={"a@b.com": "accepted"})
    api.events.return_value.patch.return_value.execute.return_value = {"id": "ev1"}
    google.sync_meeting(m)
    body = api.events.return_value.patch.call_args.kwargs["body"]
    assert body["attendees"] == [{"email": "a@b.com", "responseStatus": "accepted"}]


def test_delete_trashes_in_drive_and_pull_follows_renames(connected, api):
    customer = Customer.objects.create(name="Acme")
    keep, gone = (GoogleFile.objects.create(customer=customer, kind=k, google_id=i, name="n", url="https://u")
                  for k, i in ((GoogleFile.Kind.DOCUMENT, "d1"), (GoogleFile.Kind.SPREADSHEET, "s1")))
    files = api.files.return_value
    files.get.return_value.execute.side_effect = [{"name": "Proposal v2", "webViewLink": "https://u", "trashed": False},
                                                  {"name": "n", "trashed": True}]
    assert google.pull_files() == 2
    assert list(GoogleFile.objects.values_list("name", flat=True)) == ["Proposal v2"]
    google.delete_file(GoogleFile.objects.get())
    assert files.update.call_args.kwargs == {"fileId": "d1", "body": {"trashed": True}, "fields": "id"}
    assert not GoogleFile.objects.exists()
