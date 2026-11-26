"""Google Workspace for the CRM, with Google's official client libraries: OAuth (one connected account),
Calendar + Meet (both ways), Drive (folders, Google Docs, Google Sheets, uploads; names kept in sync) and sharing.

Scopes are the narrowest that do the job: `calendar.events` (events only) and `drive.file` (only the files and
folders the CRM itself created or uploaded; the rest of the Drive stays private).
"""

import io
import logging
import uuid
from datetime import datetime, timedelta

import httplib2
import requests
from django.db import transaction
from django.utils import timezone
from google.auth.exceptions import GoogleAuthError
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaIoBaseUpload

from crm.models import Activity, Customer, GoogleAccount, GoogleFile, Meeting

logger = logging.getLogger(__name__)

AUTH_URL = "https://accounts.google.com/o/oauth2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"
SCOPES = [
    "openid",
    "https://www.googleapis.com/auth/userinfo.email",
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/drive.file",
]
ROOT_FOLDER_NAME = "CRM Customers"
FILE_FIELDS = "id,name,webViewLink"
FILE_FIELDS_SYNC = "id,name,webViewLink,trashed"
MIME = {  # what the CRM can create; Google makes an empty Doc/Sheet from the mimeType alone
    GoogleFile.Kind.FOLDER: "application/vnd.google-apps.folder",
    GoogleFile.Kind.DOCUMENT: "application/vnd.google-apps.document",
    GoogleFile.Kind.SPREADSHEET: "application/vnd.google-apps.spreadsheet",
}
SHARE_ROLES = ("reader", "commenter", "writer")
MAX_UPLOAD_BYTES = 25 * 1024 * 1024


class GoogleError(Exception):
    """A Google call failed; the message is safe to show to staff (never contains tokens)."""


# ---- OAuth ---------------------------------------------------------------------------------


def _flow(account: GoogleAccount, redirect_uri: str) -> Flow:
    if not account.is_configured:
        raise GoogleError("Add the Google OAuth Client ID and Secret in CRM > Settings first.")
    client = {"web": {"client_id": account.client_id, "client_secret": account.client_secret,
                      "auth_uri": AUTH_URL, "token_uri": TOKEN_URL}}
    return Flow.from_client_config(client, scopes=SCOPES, redirect_uri=redirect_uri)


def auth_url(account: GoogleAccount, redirect_uri: str, state: str) -> tuple[str, str]:
    """Google's consent URL and the PKCE code verifier the callback needs."""
    flow = _flow(account, redirect_uri)
    url, _ = flow.authorization_url(access_type="offline", prompt="consent", state=state)  # consent -> refresh token
    return url, flow.code_verifier


def exchange_code(account: GoogleAccount, code: str, redirect_uri: str, code_verifier: str) -> GoogleAccount:
    """Finish the consent: store the refresh token (encrypted) and the account email."""
    flow = _flow(account, redirect_uri)
    flow.code_verifier = code_verifier
    try:
        flow.fetch_token(code=code)
    except Warning:  # oauthlib: "Scope has changed" = a permission was unticked on the consent screen
        raise GoogleError("Allow every requested permission (Calendar and Drive) and connect again.") from None
    except Exception as exc:  # oauthlib/requests errors; their text never holds the secret
        raise GoogleError(f"Google sign-in failed: {exc}") from None
    creds = flow.credentials
    if not creds.refresh_token:
        raise GoogleError("Google did not return a refresh token. Remove the app's access in your Google "
                          "account settings and connect again.")
    userinfo = _execute(build("oauth2", "v2", credentials=creds, cache_discovery=False).userinfo().get())
    account.refresh_token = creds.refresh_token
    account.email = userinfo.get("email", "")
    account.calendar_id = account.email or "primary"
    account.scopes = " ".join(creds.granted_scopes or SCOPES)
    account.connected_at = timezone.now()
    account.last_error = ""
    account.save()
    return account


def disconnect(account: GoogleAccount) -> None:
    """Revoke at Google (best effort) and forget the token. Created events and files stay in Google."""
    if account.refresh_token:
        try:
            requests.post(REVOKE_URL, data={"token": account.refresh_token}, timeout=15)
        except requests.RequestException:
            logger.warning("Could not revoke the Google token (it is removed locally anyway)")
    account.refresh_token = ""
    account.email = account.scopes = ""
    account.calendar_id = "primary"
    account.connected_at = None
    account.save()


# ---- API clients -----------------------------------------------------------------------------


def _service(name: str, version: str):
    """An authorised API client. google-auth refreshes the access token from the stored refresh token."""
    account = GoogleAccount.get_solo()
    if not account.is_connected:
        raise GoogleError("Google is not connected. Connect it in CRM > Settings.")
    creds = Credentials(None, refresh_token=account.refresh_token, token_uri=TOKEN_URL, client_id=account.client_id,
                        client_secret=account.client_secret, scopes=SCOPES)
    return build(name, version, credentials=creds, cache_discovery=False)


def _execute(request, missing_ok: bool = False) -> dict:
    try:
        return request.execute(num_retries=2) or {}
    except HttpError as exc:
        if missing_ok and exc.status_code in (404, 410):
            return {}
        raise GoogleError(exc.reason or f"HTTP {exc.status_code}") from None
    except GoogleAuthError:
        GoogleAccount.objects.update(last_error="Access expired or was revoked")
        raise GoogleError("Google access expired or was revoked: connect again in CRM > Settings.") from None
    except (OSError, httplib2.HttpLib2Error):
        raise GoogleError("Could not reach Google. Try again.") from None


# ---- Calendar + Meet -----------------------------------------------------------------------


def _event_body(meeting: Meeting) -> dict:
    who = meeting.customer
    body = {
        "summary": meeting.title,
        "description": f"{meeting.description}\n\nCRM: {who}".strip() if who else meeting.description,
        "start": {"dateTime": meeting.start_at.isoformat(), "timeZone": meeting.timezone},
        "end": {"dateTime": meeting.end_at.isoformat(), "timeZone": meeting.timezone},
        # Send the known RSVP back, so editing in the CRM does not reset the guests' answers.
        "attendees": [{"email": email, **({"responseStatus": r} if (r := meeting.guest_responses.get(email.lower())) else {})}
                      for email in meeting.attendees or []],
        "location": meeting.location,
    }
    if not meeting.location and not meeting.meet_url:
        body["conferenceData"] = {"createRequest": {"requestId": uuid.uuid4().hex,
                                                    "conferenceSolutionKey": {"type": "hangoutsMeet"}}}
    return body


def sync_meeting(meeting: Meeting) -> Meeting:
    """Create, update or cancel the Calendar event (with a Meet link when there is no location); Google emails
    the attendees. Never raises: a failure is stored on meeting.sync_error, the CRM entry is kept for a retry."""
    try:
        events = _service("calendar", "v3").events()
        calendar_id = GoogleAccount.get_solo().calendar_id
        if meeting.status == Meeting.Status.CANCELLED:
            if meeting.google_event_id:
                _execute(events.delete(calendarId=calendar_id, eventId=meeting.google_event_id, sendUpdates="all"),
                         missing_ok=True)
            event = {}
        elif meeting.google_event_id:
            event = _execute(events.patch(calendarId=calendar_id, eventId=meeting.google_event_id,
                                          body=_event_body(meeting), conferenceDataVersion=1, sendUpdates="all"))
        else:
            event = _execute(events.insert(calendarId=calendar_id, body=_event_body(meeting),
                                           conferenceDataVersion=1, sendUpdates="all"))
    except GoogleError as exc:
        meeting.sync_error = str(exc)[:1000]
        meeting.save(update_fields=["sync_error", "updated_at"])
        return meeting
    meeting.google_event_id = event.get("id", meeting.google_event_id)
    meeting.google_event_url = event.get("htmlLink", meeting.google_event_url)
    meeting.meet_url = event.get("hangoutLink") or meeting.meet_url
    meeting.sync_error = ""
    meeting.last_synced_at = timezone.now()
    meeting.save(
        update_fields=["google_event_id", "google_event_url", "meet_url", "sync_error", "last_synced_at", "updated_at"]
    )
    return meeting


RSVP = {"accepted": "accepted", "declined": "declined", "tentative": "said maybe to"}
PULL_FROM = timedelta(days=1)  # also pull meetings that ended in the last day


def _event_time(value: dict) -> datetime | None:
    return datetime.fromisoformat(value["dateTime"]) if value.get("dateTime") else None  # all-day: None


def _extract_attendee_data(event: dict) -> tuple[dict[str, str], list[dict]]:
    """Extract {email: status} and rich attendee_details list from a Google Calendar event."""
    guests: dict[str, str] = {}
    details: list[dict] = []
    for a in event.get("attendees", []):
        if not a.get("email") or a.get("self") or a.get("resource"):
            continue
        email = a["email"].lower()
        status = a.get("responseStatus", "needsAction")
        guests[email] = status
        details.append({
            "email": email,
            "name": a.get("displayName", ""),
            "status": status,
            "comment": a.get("comment", ""),
            "optional": bool(a.get("optional", False)),
            "is_organizer": bool(a.get("organizer", False)),
        })
    return guests, details


def _proposals_from_details(details: list[dict], prev_comments: dict[str, str] | None = None) -> list[dict]:
    """Build pending proposal records for any guest with a new RSVP comment (Google Calendar API sends
    proposed-time notes in attendee.comment even when responseStatus is 'accepted')."""
    prev = prev_comments or {}
    new_proposals: list[dict] = []
    for d in details:
        email, comment = d["email"], d["comment"]
        if comment and comment != prev.get(email, ""):
            new_proposals.append({
                "id": uuid.uuid4().hex[:10],
                "email": email,
                "name": d.get("name", ""),
                "proposed_start": "",
                "proposed_end": "",
                "note": comment[:500],
                "status": "pending",
                "created_at": timezone.now().isoformat(timespec="seconds"),
            })
    return new_proposals


def _apply_event_to_meeting(meeting: Meeting, event: dict) -> tuple[list[str], bool]:
    """Apply a Google Calendar event payload onto a CRM Meeting instance. Returns (changes, dirty)."""
    changes: list[str] = []
    dirty = False

    start, end = _event_time(event.get("start", {})), _event_time(event.get("end", {}))
    if start and end and (start, end) != (meeting.start_at, meeting.end_at) and end > start:
        meeting.start_at, meeting.end_at = start, end
        meeting.timezone = event["start"].get("timeZone") or meeting.timezone
        changes.append(f"rescheduled to {timezone.localtime(start):%d %b %Y, %H:%M}")
    if event.get("summary") and event["summary"][:200] != meeting.title:
        meeting.title = event["summary"][:200]
        changes.append(f"renamed to \"{meeting.title}\"")
    if event.get("location", "")[:255] != meeting.location:
        meeting.location = event.get("location", "")[:255]
        changes.append(f"location: {meeting.location or 'none'}")
    if event.get("hangoutLink") and event["hangoutLink"] != meeting.meet_url:
        meeting.meet_url = event["hangoutLink"]
        dirty = True
    if event.get("htmlLink") and event["htmlLink"] != meeting.google_event_url:
        meeting.google_event_url = event["htmlLink"]
        dirty = True

    guests, details = _extract_attendee_data(event)
    prev_comments = {d.get("email", "").lower(): d.get("comment", "") for d in (meeting.attendee_details or [])}
    for d in details:
        email, answer, comment = d["email"], d["status"], d["comment"]
        if answer != meeting.guest_responses.get(email) and answer in RSVP:
            changes.append(f"{email} {RSVP[answer]} the invite")
        if comment and comment != prev_comments.get(email, ""):
            changes.append(f"{email} left a note: \"{comment[:120]}\"")
    if new_props := _proposals_from_details(details, prev_comments):
        meeting.proposed_times = [*new_props, *(meeting.proposed_times or [])]
        dirty = True

    known = {email.lower() for email in meeting.attendees}
    if added := sorted(set(guests) - known):
        changes.append(f"guests added: {', '.join(added)}")
    if removed := sorted(known - set(guests)):
        changes.append(f"guests removed: {', '.join(removed)}")
    if guests != meeting.guest_responses or set(guests) != known or details != (meeting.attendee_details or []):
        meeting.attendees = list(guests)
        meeting.guest_responses = guests
        meeting.attendee_details = details
        dirty = True
    return changes, dirty


def pull_meeting(meeting: Meeting, events=None, calendar_id: str | None = None, event: dict | None = None) -> list[str]:
    """Copy what changed in Google Calendar (cancelled, rescheduled, renamed, moved, guests' RSVPs & comments)
    into the CRM meeting and log it. Returns the changes. Google wins only when its copy is newer than the CRM's,
    so a CRM edit that is still being pushed is never overwritten."""
    from crm import services  # crm.services imports meta_connector; not needed when google.py loads

    if event is None:
        if events is None:
            events = _service("calendar", "v3").events()
        if calendar_id is None:
            calendar_id = GoogleAccount.get_solo().calendar_id
        event = _execute(events.get(calendarId=calendar_id, eventId=meeting.google_event_id), missing_ok=True)

    changes, dirty = [], False
    if not event or event.get("status") == "cancelled":
        meeting.status = Meeting.Status.CANCELLED
        changes.append("cancelled in Google Calendar")
    else:
        if event.get("updated") and datetime.fromisoformat(event["updated"]) <= meeting.updated_at:
            return []
        changes, dirty = _apply_event_to_meeting(meeting, event)

    if changes or dirty:
        meeting.last_synced_at = timezone.now()
        meeting.save()
    if changes and meeting.customer_id:
        services.add_note(user=None, customer=meeting.customer, opportunity=meeting.opportunity,
                          kind=Activity.Kind.MEETING,
                          body=f"Meeting {meeting.title}: " + "; ".join(changes))
    return changes


def pull_meetings() -> int:
    """Pull Google Calendar changes into every upcoming synced CRM meeting. Returns how many changed."""
    meetings = Meeting.objects.select_related("customer", "opportunity").filter(
        status=Meeting.Status.SCHEDULED, sync_error="", end_at__gte=timezone.now() - PULL_FROM,
    ).exclude(google_event_id="")
    if not meetings:
        return 0
    events, calendar_id = _service("calendar", "v3").events(), GoogleAccount.get_solo().calendar_id
    changed = 0
    for meeting in meetings:
        try:
            changed += bool(pull_meeting(meeting, events, calendar_id))
        except GoogleError as exc:
            logger.warning("Could not pull meeting %s from Google Calendar: %s", meeting.pk, exc)
    return changed


def import_calendar_events(days_back: int = 1, days_ahead: int = 30) -> dict[str, int]:
    """Full two-way pull from Google Calendar: updates existing CRM meetings and imports new Google Calendar events."""
    from crm import services

    events_api = _service("calendar", "v3").events()
    calendar_id = GoogleAccount.get_solo().calendar_id
    now = timezone.now()
    time_min = (now - timedelta(days=days_back)).isoformat()
    time_max = (now + timedelta(days=days_ahead)).isoformat()

    payload = _execute(events_api.list(
        calendarId=calendar_id,
        timeMin=time_min,
        timeMax=time_max,
        singleEvents=True,
        orderBy="startTime",
        maxResults=100,
    ))
    items = payload.get("items", [])
    existing_by_id = {
        m.google_event_id: m
        for m in Meeting.objects.select_related("customer", "opportunity").exclude(google_event_id="")
    }
    created = 0
    updated = 0

    for event in items:
        event_id = event.get("id")
        if not event_id:
            continue
        if event_id in existing_by_id:
            if pull_meeting(existing_by_id[event_id], event=event):
                updated += 1
            continue
        if event.get("status") == "cancelled":
            continue
        start, end = _event_time(event.get("start", {})), _event_time(event.get("end", {}))
        if not start or not end or end <= start:
            continue

        guests, details = _extract_attendee_data(event)
        matched_customer = None
        for email in guests:
            matched_customer = services.match_customer(email=email)
            if matched_customer:
                break

        meeting = Meeting.objects.create(
            title=(event.get("summary") or "Untitled meeting")[:200],
            description=(event.get("description") or "")[:5000],
            customer=matched_customer,
            start_at=start,
            end_at=end,
            timezone=event.get("start", {}).get("timeZone") or "Asia/Kolkata",
            location=(event.get("location") or "")[:255],
            attendees=list(guests),
            guest_responses=guests,
            attendee_details=details,
            proposed_times=_proposals_from_details(details),
            google_event_id=event_id,
            google_event_url=event.get("htmlLink", ""),
            meet_url=event.get("hangoutLink", ""),
            last_synced_at=now,
        )
        created += 1
        if matched_customer:
            services.add_note(
                user=None,
                customer=matched_customer,
                kind=Activity.Kind.MEETING,
                body=f"Meeting \"{meeting.title}\" synced from Google Calendar ({timezone.localtime(start):%d %b %Y, %H:%M}).",
            )

    return {"created": created, "updated": updated}


# ---- Drive ---------------------------------------------------------------------------------


def _new_item(files, name: str, kind: str, parent: str | None) -> dict:
    body = {"name": name[:255], "mimeType": MIME[kind], **({"parents": [parent]} if parent else {})}
    return _execute(files.create(body=body, fields=FILE_FIELDS))


def customer_folder(customer: Customer, files=None) -> str:
    """The customer's own Drive folder inside "CRM Customers" (both created on first use)."""
    files = files or _service("drive", "v3").files()
    with transaction.atomic():
        account = GoogleAccount.objects.select_for_update().get(pk=GoogleAccount.get_solo().pk)
        if not account.root_folder_id:
            account.root_folder_id = _new_item(files, ROOT_FOLDER_NAME, GoogleFile.Kind.FOLDER, None)["id"]
            account.save(update_fields=["root_folder_id"])
        customer = Customer.objects.select_for_update().get(pk=customer.pk)  # two clicks never make two folders
        if not customer.drive_folder_id:
            name = f"{customer.name} — {customer.company}" if customer.company else customer.name
            folder = _new_item(files, name, GoogleFile.Kind.FOLDER, account.root_folder_id)
            customer.drive_folder_id, customer.drive_folder_url = folder["id"], folder.get("webViewLink", "")
            customer.save(update_fields=["drive_folder_id", "drive_folder_url", "updated_at"])
        return customer.drive_folder_id


def create_file(customer: Customer, kind: str, name: str, user=None) -> GoogleFile:
    """A new, empty Google Doc / Google Sheet / sub-folder with this name in the customer's folder."""
    name = name.strip()
    if kind not in MIME:
        raise GoogleError(f"Cannot create a {kind!r} here.")
    if not name:
        raise GoogleError("Give it a name.")
    files = _service("drive", "v3").files()
    item = _new_item(files, name, kind, customer_folder(customer, files))
    return GoogleFile.objects.create(customer=customer, kind=kind, google_id=item["id"], name=item.get("name", name),
                                     url=item.get("webViewLink", ""), created_by=user)


def delete_file(gfile: GoogleFile) -> None:
    """Move the item to Google Drive's trash (restorable there for 30 days) and remove it from the CRM."""
    _execute(_service("drive", "v3").files().update(fileId=gfile.google_id, body={"trashed": True}, fields="id"),
             missing_ok=True)  # already gone in Google: just forget it here
    gfile.delete()


def pull_files() -> int:
    """Copy renames made in Google Drive into the CRM, and forget items trashed or deleted there.
    Returns how many changed."""
    # shortcut: one files.get per item; fine for a few hundred files, switch to changes.list + pageToken beyond that
    gfiles = list(GoogleFile.objects.order_by("pk"))
    if not gfiles:
        return 0
    files, changed = _service("drive", "v3").files(), 0
    for gfile in gfiles:
        try:
            item = _execute(files.get(fileId=gfile.google_id, fields=FILE_FIELDS_SYNC), missing_ok=True)
        except GoogleError as exc:
            logger.warning("Could not read Drive item %s: %s", gfile.google_id, exc)
            continue
        if not item or item.get("trashed"):
            gfile.delete()
            changed += 1
        elif (item.get("name", gfile.name), item.get("webViewLink", gfile.url)) != (gfile.name, gfile.url):
            gfile.name, gfile.url = item.get("name", gfile.name)[:255], item.get("webViewLink", gfile.url)
            gfile.save(update_fields=["name", "url"])
            changed += 1
    return changed


def upload_text_as_doc(customer: Customer, name: str, content: str, user=None) -> GoogleFile:
    """Create a native Google Doc in the customer's Drive folder populated with plain-text content."""
    files = _service("drive", "v3").files()
    media = MediaIoBaseUpload(io.BytesIO(content.encode("utf-8")), mimetype="text/plain", resumable=False)
    body = {
        "name": name[:255],
        "mimeType": MIME[GoogleFile.Kind.DOCUMENT],
        "parents": [customer_folder(customer, files)],
    }
    item = _execute(files.create(body=body, media_body=media, fields=FILE_FIELDS))
    return GoogleFile.objects.create(
        customer=customer,
        kind=GoogleFile.Kind.DOCUMENT,
        google_id=item["id"],
        name=item.get("name", name),
        url=item.get("webViewLink", ""),
        created_by=user,
    )


def upload_file(customer: Customer, uploaded, user=None) -> GoogleFile:
    """Upload a file (a Django UploadedFile, up to 25 MB) into the customer's folder."""
    if uploaded.size > MAX_UPLOAD_BYTES:
        raise GoogleError("Files up to 25 MB can be uploaded from the CRM.")
    files = _service("drive", "v3").files()
    media = MediaIoBaseUpload(uploaded, mimetype=uploaded.content_type or "application/octet-stream")
    body = {"name": uploaded.name[:255], "parents": [customer_folder(customer, files)]}
    item = _execute(files.create(body=body, media_body=media, fields=FILE_FIELDS))
    return GoogleFile.objects.create(customer=customer, kind=GoogleFile.Kind.FILE, google_id=item["id"],
                                     name=item.get("name", uploaded.name), url=item.get("webViewLink", ""),
                                     created_by=user)


def share(google_id: str, email: str, role: str = "reader") -> dict:
    """Give one person access to a CRM file or folder; Google emails them the link. Never hands out ownership."""
    if role not in SHARE_ROLES:
        raise GoogleError(f"Role must be one of {', '.join(SHARE_ROLES)}.")
    permissions = _service("drive", "v3").permissions()
    return _execute(permissions.create(fileId=google_id, body={"type": "user", "role": role, "emailAddress": email},
                                       sendNotificationEmail=True, fields="id"))


def share_file(gfile: GoogleFile, email: str, role: str = "reader") -> GoogleFile:
    share(gfile.google_id, email, role)
    gfile.shared_with = [*(s for s in gfile.shared_with if s.get("email") != email),
                         {"email": email, "role": role, "at": timezone.now().isoformat(timespec="seconds")}]
    gfile.save(update_fields=["shared_with"])
    return gfile
