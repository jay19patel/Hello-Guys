"""Google Workspace for the CRM, with Google's official client libraries: OAuth (one connected account),
Calendar + Meet (both ways), Drive (folders, Google Docs, Google Sheets, uploads; names kept in sync) and sharing.

Scopes are the narrowest that do the job: `calendar.events` (events only) and `drive.file` (only the files and
folders the CRM itself created or uploaded; the rest of the Drive stays private).
"""

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
    who = meeting.customer or meeting.lead
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
    meeting.save(update_fields=["google_event_id", "google_event_url", "meet_url", "sync_error", "updated_at"])
    return meeting


RSVP = {"accepted": "accepted", "declined": "declined", "tentative": "said maybe to"}
PULL_FROM = timedelta(days=1)  # also pull meetings that ended in the last day


def _event_time(value: dict) -> datetime | None:
    return datetime.fromisoformat(value["dateTime"]) if value.get("dateTime") else None  # all-day: None


def pull_meeting(meeting: Meeting, events, calendar_id: str) -> list[str]:
    """Copy what changed in Google Calendar (cancelled, rescheduled, renamed, moved, guests' RSVPs) into the CRM
    meeting and log it. Returns the changes. Google wins only when its copy is newer than the CRM's, so a CRM
    edit that is still being pushed is never overwritten."""
    from crm import services  # crm.services imports meta_connector; not needed when google.py loads

    event = _execute(events.get(calendarId=calendar_id, eventId=meeting.google_event_id), missing_ok=True)
    changes, dirty = [], False
    if not event or event.get("status") == "cancelled":
        meeting.status = Meeting.Status.CANCELLED
        changes.append("cancelled in Google Calendar")
    else:
        if datetime.fromisoformat(event["updated"]) <= meeting.updated_at:
            return []
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
        meeting.meet_url = event.get("hangoutLink") or meeting.meet_url
        guests = {a["email"].lower(): a.get("responseStatus", "needsAction") for a in event.get("attendees", [])
                  if a.get("email") and not a.get("self") and not a.get("resource")}
        for email, answer in guests.items():
            if answer != meeting.guest_responses.get(email) and answer in RSVP:
                changes.append(f"{email} {RSVP[answer]} the invite")
        known = {email.lower() for email in meeting.attendees}
        if added := sorted(set(guests) - known):
            changes.append(f"guests added: {', '.join(added)}")
        if removed := sorted(known - set(guests)):
            changes.append(f"guests removed: {', '.join(removed)}")
        if guests != meeting.guest_responses or set(guests) != known:  # "needsAction" is saved but not logged
            meeting.attendees, meeting.guest_responses, dirty = list(guests), guests, True
    if changes or dirty:
        meeting.save()
    if changes:
        services.add_note(user=None, customer=meeting.customer, opportunity=meeting.opportunity,
                          kind=Activity.Kind.MEETING,
                          body=f"Meeting {meeting.title}: " + "; ".join(changes))
    return changes


def pull_meetings() -> int:
    """Pull Google Calendar changes into every upcoming synced CRM meeting. Returns how many changed."""
    # shortcut: one events.get per meeting; fine for tens of meetings, switch to events.list + syncToken for hundreds
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
