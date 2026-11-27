"""AI tools over CRM Customers and Opportunities (autodiscovered by hello_guy_ai).

Checks the run user's Django model permissions for reads/updates, and captures leads directly into CRM.
"""

from datetime import date as dt_date
from typing import Any, Literal

from django.contrib.auth import get_permission_codename
from django.core.validators import validate_email
from django.db.models import Count, Model, Q
from django.utils import timezone as dj_tz

from crm import services
from crm.models import Customer, Meeting, Opportunity, Service, Source
from hello_guy_ai import ToolError, current_context, tool
from hello_guy_ai.services.scheduling import resolve_requested_slot, zone

MAX_LIMIT = 50
OPPORTUNITY_FIELDS = (
    "reference",
    "title",
    "customer__name",
    "customer__email",
    "service__name",
    "source",
    "status",
    "form_type",
    "created_at",
)
STATUSES = Literal["new", "contacted", "qualified", "proposal", "won", "lost"]


def _require(action: str, model: type[Model]) -> None:
    user = current_context().user
    if not user.has_perm(f"{model._meta.app_label}.{get_permission_codename(action, model._meta)}"):
        raise ToolError(f"You don't have permission to {action} {model._meta.verbose_name_plural}.")


def _limit(limit: int) -> int:
    return max(1, min(int(limit), MAX_LIMIT))


@tool
def list_opportunities(
    status: Literal["open", "new", "contacted", "qualified", "proposal", "won", "lost", "all"] = "open",
    query: str = "",
    unreplied_only: bool = False,
    limit: int = 10,
) -> list[dict]:
    """List inquiries / opportunities, newest first. status "open" = not won or lost yet;
    unreplied_only=true returns only status "new". query matches title, details, service or customer fields.
    Contains personal data: only share it with the user who asked."""
    _require("view", Opportunity)
    qs = Opportunity.objects.order_by("-created_at")
    if unreplied_only:
        qs = qs.filter(status=Opportunity.Status.NEW)
    elif status == "open":
        qs = qs.filter(status__in=Opportunity.OPEN)
    elif status != "all":
        qs = qs.filter(status=status)
    if query:
        qs = qs.filter(
            Q(title__icontains=query)
            | Q(details__icontains=query)
            | Q(service__name__icontains=query)
            | Q(customer__name__icontains=query)
            | Q(customer__email__icontains=query)
            | Q(customer__company__icontains=query)
        )
    return list(qs.values(*OPPORTUNITY_FIELDS)[: _limit(limit)])


@tool
def get_opportunity(reference: str) -> dict:
    """Get one opportunity / inquiry with its details, customer, services and activity timeline by reference."""
    _require("view", Opportunity)
    opp = Opportunity.objects.select_related("customer", "service", "assigned_to").filter(reference=reference).first()
    if opp is None:
        raise ToolError(f"No opportunity {reference!r}. Use list_opportunities to find the reference.")
    c = opp.customer
    return {
        "reference": opp.reference,
        "title": opp.title,
        "details": opp.details,
        "status": opp.status,
        "service": opp.service.name if opp.service else None,
        "services": list(opp.services.values_list("name", flat=True)),
        "source": opp.source,
        "form_type": opp.form_type,
        "created_at": opp.created_at,
        "customer": {"name": c.name, "email": c.email, "phone": c.phone, "company": c.company},
        "assigned_to": opp.assigned_to.get_username() if opp.assigned_to else None,
        "activity": list(opp.activities.values("kind", "body", "created_at")[:20]),
    }


def _confirm_status(args: dict) -> str:
    note = f"\nNote: {args['note']}" if args.get("note") else ""
    return f"Set opportunity {args['reference']} to {args['status']}.{note}"


@tool(requires_confirmation=True, confirm_prompt=_confirm_status)
def update_opportunity_status(reference: str, status: STATUSES, note: str = "") -> dict:
    """Change an opportunity's status (e.g. contacted after replying) and log an optional note. Needs approval."""
    _require("change", Opportunity)
    opp = Opportunity.objects.select_related("customer").filter(reference=reference).first()
    if opp is None:
        raise ToolError(f"No opportunity {reference!r}.")
    services.set_status(opp, status, current_context().user, note=note[:1000])
    return {"reference": reference, "status": status}


def _match_services(raw_service: str) -> list[Service]:
    matched: list[Service] = []
    for part in (raw_service or "").split(","):
        if term := part.strip():
            if svc := Service.objects.filter(is_active=True, name__icontains=term).first():
                if svc not in matched:
                    matched.append(svc)
    return matched


@tool
def create_inquiry(
    name: str,
    message: str,
    email: str = "",
    phone: str = "",
    subject: str = "",
    service: str = "",
    preferred_date: str = "",
    preferred_time: str = "",
) -> dict:
    """Save a visitor's project inquiry / contact request directly into CRM as Customer + Opportunity.
    On website chat, require confirmed email; on WhatsApp/Instagram DMs, email is optional if phone or chat
    contact is known. Supports comma-separated `service` names, `preferred_date` ('YYYY-MM-DD'), and
    optional 24h `preferred_time` ('HH:MM' — leave blank to auto-schedule a day-before confirmation follow-up).
    Returns the reference / inquiry_id to share with the visitor."""
    from cms.views import check_maintenance  # same switch as the website's contact form
    from hello_guy_ai.models import Message, Thread

    _require("add", Opportunity)
    ctx = current_context()
    done = Message.objects.filter(run_id=ctx.run_id, name="create_inquiry").exclude(content__startswith="Error:")
    if ctx.run_id and done.exists():
        raise ToolError("An inquiry was already submitted in this reply. Do not submit it again.")

    thread = Thread.objects.filter(pk=ctx.thread_id).first() if ctx.thread_id else None
    contact = services.contact_for_thread(thread) if thread else None
    if contact is None and check_maintenance("inquiries") is not None:
        raise ToolError("Inquiries are temporarily closed. Ask the visitor to try again later.")

    name, email, phone, message = name.strip(), email.strip(), phone.strip(), message.strip()
    if len(name) < 2:
        raise ToolError("Ask the visitor for their name.")
    if email:
        try:
            validate_email(email)
        except Exception:
            raise ToolError("That email address looks invalid. Ask the visitor to check it.") from None
    elif not phone and contact is None:
        raise ToolError("Ask the visitor for a valid email address so we can follow up.")
    if len(message) < 5:
        raise ToolError("Ask the visitor to describe what they need.")

    tz = zone(ctx.timezone)
    slot_dt, day_before = None, False
    pref_date, pref_time = (preferred_date or "").strip(), (preferred_time or "").strip()
    if pref_date:
        try:
            slot_dt, day_before = resolve_requested_slot(date_str=pref_date, time_str=pref_time, tz=tz)
        except ValueError as exc:
            raise ToolError(str(exc)) from None

    matched_services = _match_services(service)
    opp = services.create_opportunity(
        name=name[:150],
        email=email,
        phone=phone[:50] or (contact.phone if contact else ""),
        title=(subject.strip() or message.split("\n")[0])[:255],
        details=message[:5000],
        services_list=matched_services,
        source=services.THREAD_SOURCES.get(contact.platform, Source.WEBSITE_CHAT) if contact else Source.WEBSITE_CHAT,
        form_type=f"{contact.platform_account.get_platform_display()} Bot" if contact else "AI Chat Assistant",
        thread=thread,
        contact=contact,
        default_followup=slot_dt is None,
    )

    out: dict[str, Any] = {
        "inquiry_id": opp.reference,
        "reference": opp.reference,
        "customer_id": opp.customer_id,
        "services": [s.name for s in matched_services],
        "status": "received",
    }
    if slot_dt is not None:
        req_date_str = pref_date.split("T")[0]
        try:
            req_label = dt_date.fromisoformat(req_date_str).strftime("%d %b %Y")
        except ValueError:
            req_label = req_date_str
        if day_before:
            fu_note = (
                f"Visitor requested meeting on {req_label} (no time specified) — "
                "follow up to ask and confirm a time for tomorrow."
            )
        else:
            local_slot = dj_tz.localtime(slot_dt, tz)
            fu_note = f"Requested meeting for {local_slot:%d %b %Y} at {local_slot.strftime('%I:%M %p').lstrip('0')}."
        fu = services.schedule_follow_up(
            customer=opp.customer,
            opportunity=opp,
            due_at=slot_dt,
            channel="Meeting",
            note=fu_note,
            assigned_to=services.default_staff_assignee(),
        )
        out.update({
            "follow_up_id": fu.pk,
            "follow_up_at": fu.due_at.astimezone(tz).isoformat(),
            "follow_up_note": fu_note,
            "scheduled_for_day_before": day_before,
            "needs_time_confirmation": day_before,
        })
    return out


@tool
def list_customers(query: str = "", limit: int = 10) -> list[dict]:
    """List customers by name, with how many open opportunities each has. query matches name, company, email,
    phone or Instagram username."""
    _require("view", Customer)
    qs = Customer.objects.all()
    if query:
        qs = qs.filter(
            Q(name__icontains=query)
            | Q(company__icontains=query)
            | Q(email__icontains=query)
            | Q(phone__icontains=query)
            | Q(instagram_username__icontains=query)
        )
    qs = qs.annotate(open_opportunities=Count("opportunities", filter=Q(opportunities__status__in=Opportunity.OPEN)))
    return [
        {
            "name": c.name,
            "company": c.company,
            "email": c.email,
            "phone": c.phone,
            "source": c.source,
            "open_opportunities": c.open_opportunities,
        }
        for c in qs.order_by("-created_at")[: _limit(limit)]
    ]


@tool
def list_meetings(upcoming_only: bool = True, limit: int = 10) -> list[dict]:
    """List scheduled CRM meetings with customers."""
    _require("view", Meeting)
    qs = Meeting.objects.select_related("customer", "opportunity").order_by("start_at")
    if upcoming_only:
        qs = qs.filter(status=Meeting.Status.SCHEDULED)
    return [
        {
            "title": m.title,
            "customer": m.customer.name if m.customer else None,
            "start_at": m.start_at.isoformat(),
            "status": m.status,
            "meet_url": m.meet_url,
        }
        for m in qs[: _limit(limit)]
    ]


def _confirm_email(args: dict) -> str:
    return f"Send email to {args.get('to_email') or 'customer'}\nSubject: {args.get('subject')}\n\n{args.get('body', '')[:400]}"


@tool(requires_confirmation=True, confirm_prompt=_confirm_email)
def send_customer_email(
    customer_id: int,
    subject: str,
    body: str,
    to_email: str = "",
    opportunity_reference: str = "",
) -> dict:
    """Send an email to a CRM customer (via Gmail API if connected, else SMTP) and log it. Needs approval."""
    from crm import email_service

    _require("change", Customer)
    customer = Customer.objects.filter(pk=customer_id).first()
    if customer is None:
        raise ToolError(f"No customer #{customer_id}.")
    opp = Opportunity.objects.filter(reference=opportunity_reference.strip(), customer=customer).first() if opportunity_reference.strip() else None
    try:
        msg = email_service.send_crm_email(
            customer=customer,
            to_email=to_email or customer.email,
            subject=subject,
            body=body,
            opportunity=opp,
            user=current_context().user,
        )
    except Exception as exc:
        raise ToolError(str(exc)) from None
    return {"email_id": msg.pk, "to": msg.recipient, "subject": msg.subject, "status": "sent"}
