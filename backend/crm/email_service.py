"""Two-way CRM Email service using Gmail API (when GoogleAccount is connected) with Django SMTP fallback."""

import base64
from email.message import EmailMessage as MimeEmail
from email.utils import parseaddr
from collections import defaultdict
import logging
import re

from django.conf import settings
from django.core.mail import EmailMessage as DjangoEmailMessage
from django.utils import timezone

from core import notifications
from crm import google, services
from crm.models import Activity, Customer, EmailMessage, GoogleAccount, Opportunity

logger = logging.getLogger(__name__)


_RE_PREFIX = re.compile(r"^(?:re|fwd|fw)\s*:\s*", re.IGNORECASE)

_SENTIMENT_RULES: list[tuple[str, tuple[str, ...]]] = [
    (EmailMessage.Sentiment.ANGRY, ("angry", "unacceptable", "worst", "frustrated", "complaint", "furious", "terrible", "cancel", "refund", "escalate")),
    (EmailMessage.Sentiment.SAD, ("sad", "sorry", "disappointed", "unfortunately", "regret", "missed", "unable to", "delay")),
    (EmailMessage.Sentiment.JOY, ("thrilled", "amazing", "awesome", "fantastic", "love it", "excited", "thank you so much", "approved", "deal closed", "congrats")),
    (EmailMessage.Sentiment.HAPPY, ("thanks", "thank you", "appreciate", "sounds good", "happy", "glad", "great", "confirmed", "talk soon", "looking forward")),
    (EmailMessage.Sentiment.CONFUSED, ("?", "clarify", "not sure", "confused", "can we", "could you", "how does", "what is", "please explain")),
]


def detect_email_sentiment(subject: str, body: str) -> str:
    """Classify an email's tone into a Sentiment choice using keyword rules."""
    text = f"{subject} {body}".lower()
    for sentiment, keywords in _SENTIMENT_RULES:
        if any(kw in text for kw in keywords):
            return sentiment
    return EmailMessage.Sentiment.NEUTRAL


def group_email_threads(emails) -> list[dict]:
    """Group EmailMessage records into conversation threads ordered newest-first."""
    buckets: dict[str, list[EmailMessage]] = defaultdict(list)
    for em in emails:
        if em.gmail_thread_id:
            key = f"tid:{em.gmail_thread_id}"
        else:
            norm = _RE_PREFIX.sub("", (em.subject or "").strip()).strip().lower()
            key = f"sub:{norm or em.pk}"
        buckets[key].append(em)

    threads = []
    for msgs in buckets.values():
        ordered = sorted(msgs, key=lambda m: m.sent_at)
        threads.append({
            "root": ordered[0],
            "replies": ordered[1:],
            "latest_at": ordered[-1].sent_at,
            "latest": ordered[-1],
            "count": len(ordered),
        })
    threads.sort(key=lambda t: t["latest_at"], reverse=True)
    return threads


def _has_gmail_scope(account: GoogleAccount, scope_suffix: str) -> bool:
    return account.is_connected and (f"gmail.{scope_suffix}" in (account.scopes or ""))


def send_crm_email(
    *,
    customer: Customer,
    to_email: str,
    subject: str,
    body: str,
    opportunity: Opportunity | None = None,
    user=None,
) -> EmailMessage:
    """Send an email to a customer via Gmail API (if connected) or Django SMTP, and log it in CRM."""
    to_email = (to_email or customer.email or "").strip()
    if not to_email:
        raise ValueError("Customer has no email address.")
    subject = (subject or "").strip()[:255]
    body = (body or "").strip()[:20000]
    if not subject or not body:
        raise ValueError("Subject and message body are required.")

    account = GoogleAccount.get_solo()
    sender_email = account.email or getattr(settings, "DEFAULT_FROM_EMAIL", "") or "noreply@localhost"
    gmail_msg_id = ""
    gmail_thread_id = ""

    last_thread = (
        EmailMessage.objects.filter(customer=customer, gmail_thread_id__gt="")
        .order_by("-sent_at")
        .values_list("gmail_thread_id", flat=True)
        .first()
    )

    if _has_gmail_scope(account, "send"):
        mime = MimeEmail()
        mime["To"] = to_email
        mime["From"] = sender_email
        mime["Subject"] = subject
        mime.set_content(body)
        raw = base64.urlsafe_b64encode(mime.as_bytes()).decode("ascii")
        payload = {"raw": raw}
        if last_thread:
            payload["threadId"] = last_thread
        svc = google._service("gmail", "v1")
        sent = google._execute(svc.users().messages().send(userId="me", body=payload))
        gmail_msg_id = sent.get("id", "")
        gmail_thread_id = sent.get("threadId", "") or last_thread or ""
    else:
        mail = DjangoEmailMessage(subject=subject, body=body, from_email=sender_email, to=[to_email])
        mail.send(fail_silently=False)

    record = EmailMessage.objects.create(
        customer=customer,
        opportunity=opportunity,
        direction=EmailMessage.Direction.OUTGOING,
        sentiment=detect_email_sentiment(subject, body),
        sender=sender_email,
        recipient=to_email,
        subject=subject,
        body=body,
        gmail_message_id=gmail_msg_id,
        gmail_thread_id=gmail_thread_id,
        sent_by=user if getattr(user, "is_authenticated", False) else None,
        sent_at=timezone.now(),
    )
    services.add_note(
        user=user if getattr(user, "is_authenticated", False) else None,
        customer=customer,
        opportunity=opportunity,
        kind=Activity.Kind.EMAIL,
        body=f"Email sent to {to_email} · {subject}\n\n{body[:500]}",
    )
    return record


def _extract_plain_body(payload: dict) -> str:
    if body_data := (payload.get("body") or {}).get("data"):
        try:
            return base64.urlsafe_b64decode(body_data).decode("utf-8", errors="replace").strip()
        except Exception:
            return ""
    for part in payload.get("parts") or []:
        if part.get("mimeType") == "text/plain" and (part.get("body") or {}).get("data"):
            try:
                return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace").strip()
            except Exception:
                continue
        if nested := _extract_plain_body(part):
            return nested
    return ""


def sync_inbound_emails(limit: int = 25) -> int:
    """Pull recent Gmail messages matching known CRM customers and log incoming replies."""
    account = GoogleAccount.get_solo()
    if not _has_gmail_scope(account, "readonly"):
        return 0

    svc = google._service("gmail", "v1")
    listing = google._execute(
        svc.users().messages().list(userId="me", q="newer_than:14d -from:me", maxResults=max(1, min(limit, 50)))
    )
    messages_meta = listing.get("messages") or []
    if not messages_meta:
        return 0

    account_email = (account.email or "").lower()
    imported = 0
    for item in messages_meta:
        msg_id = item.get("id", "")
        if not msg_id or EmailMessage.objects.filter(gmail_message_id=msg_id).exists():
            continue
        detail = google._execute(svc.users().messages().get(userId="me", id=msg_id, format="full"))
        headers = {h["name"].lower(): h["value"] for h in (detail.get("payload") or {}).get("headers") or []}
        _, from_addr = parseaddr(headers.get("from", ""))
        from_addr = from_addr.strip().lower()
        if not from_addr or from_addr == account_email:
            continue
        customer = Customer.objects.filter(email__iexact=from_addr).first()
        if customer is None:
            continue

        subject = (headers.get("subject") or "(No subject)")[:255]
        thread_id = detail.get("threadId", "")
        body = (_extract_plain_body(detail.get("payload") or {}) or detail.get("snippet") or "")[:20000]
        opp = (
            customer.opportunities.filter(emails__gmail_thread_id=thread_id).first()
            if thread_id
            else customer.opportunities.filter(status__in=Opportunity.OPEN).first()
        )
        EmailMessage.objects.create(
            customer=customer,
            opportunity=opp,
            direction=EmailMessage.Direction.INCOMING,
            sentiment=detect_email_sentiment(subject, body),
            sender=from_addr,
            recipient=account_email or from_addr,
            subject=subject,
            body=body,
            gmail_message_id=msg_id,
            gmail_thread_id=thread_id,
            sent_at=timezone.now(),
        )
        services.add_note(
            user=None,
            customer=customer,
            opportunity=opp,
            kind=Activity.Kind.EMAIL,
            body=f"Email received from {from_addr} · {subject}\n\n{body[:500]}",
        )
        notifications.notify_staff(
            title=f"Email reply from {customer.name}",
            body=f"{subject} — {body[:140]}",
            url=f"/crm/customers/{customer.pk}/",
            category="crm",
            level="info",
        )
        imported += 1
    return imported
