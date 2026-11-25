"""AI tools over the CRM (autodiscovered by hello_guy_ai). Each checks the run user's Django permissions."""

from typing import Literal

from django.contrib.auth import get_permission_codename
from django.core.validators import validate_email
from django.db.models import Count, Model, Q

from crm import services
from crm.models import Customer, Meeting, Opportunity, Service
from hello_guy_ai import ToolError, current_context, tool

MAX_LIMIT = 50
OPPORTUNITY_FIELDS = ("reference", "title", "customer__name", "customer__email", "service__name", "source", "status",
                      "created_at")
STATUSES = Literal["new", "contacted", "qualified", "proposal", "won", "lost"]


def _require(action: str, model: type[Model]) -> None:
    user = current_context().user
    if not user.has_perm(f"{model._meta.app_label}.{get_permission_codename(action, model._meta)}"):
        raise ToolError(f"You don't have permission to {action} {model._meta.verbose_name_plural}.")


def _limit(limit: int) -> int:
    return max(1, min(int(limit), MAX_LIMIT))


@tool
def list_opportunities(status: Literal["open", "new", "contacted", "qualified", "proposal", "won", "lost", "all"] = "open",
                       query: str = "", limit: int = 10) -> list[dict]:
    """List what customers are interested in (opportunities), newest first. status "open" = not won or lost yet.
    query matches the title, details, service or customer name/email/company. Contains personal data: only
    share it with the user who asked."""
    _require("view", Opportunity)
    qs = Opportunity.objects.order_by("-created_at")
    if status == "open":
        qs = qs.filter(status__in=Opportunity.OPEN)
    elif status != "all":
        qs = qs.filter(status=status)
    if query:
        qs = qs.filter(Q(title__icontains=query) | Q(details__icontains=query) | Q(service__name__icontains=query)
                       | Q(customer__name__icontains=query) | Q(customer__email__icontains=query)
                       | Q(customer__company__icontains=query))
    return list(qs.values(*OPPORTUNITY_FIELDS)[: _limit(limit)])


@tool
def get_opportunity(reference: str) -> dict:
    """Get one opportunity with its details, customer and activity timeline, by reference (e.g. opp_74658245fdf5)."""
    _require("view", Opportunity)
    opp = Opportunity.objects.select_related("customer", "service", "assigned_to").filter(reference=reference).first()
    if opp is None:
        raise ToolError(f"No opportunity {reference!r}. Use list_opportunities to find the reference.")
    c = opp.customer
    return {
        "reference": opp.reference, "title": opp.title, "details": opp.details, "status": opp.status,
        "service": opp.service.name if opp.service else None, "source": opp.source, "created_at": opp.created_at,
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


@tool
def create_opportunity(name: str, message: str, email: str = "", phone: str = "", service: str = "") -> dict:
    """Save the visitor's project inquiry as a Customer + Opportunity so the team can follow up.
    `service`: the service they want, if clear. On WhatsApp/Instagram, `email` is optional if `phone` or
    the chat contact is already known. Returns the reference to share with the visitor."""
    from hello_guy_ai.models import Message, Thread

    _require("add", Opportunity)
    ctx = current_context()
    done = Message.objects.filter(run_id=ctx.run_id, name="create_opportunity").exclude(content__startswith="Error:")
    if ctx.run_id and done.exists():
        raise ToolError("The inquiry was already saved in this reply. Do not save it again.")
    name, email, phone, message = name.strip(), email.strip(), phone.strip(), message.strip()
    if len(name) < 2:
        raise ToolError("Ask the visitor for their name.")
    thread = Thread.objects.filter(pk=ctx.thread_id).first() if ctx.thread_id else None
    contact = services.contact_for_thread(thread) if thread else None
    if email:
        try:
            validate_email(email)
        except Exception:
            raise ToolError("That email address looks invalid. Ask the visitor to check it.") from None
    elif not phone and contact is None:
        raise ToolError("Ask the visitor for an email address or phone number so we can follow up.")
    if len(message) < 5:
        raise ToolError("Ask the visitor to describe what they need.")
    matched = Service.objects.filter(is_active=True, name__icontains=service.strip()).first() if service.strip() else None
    opp = services.create_opportunity(
        name=name,
        email=email,
        phone=phone or (contact.phone if contact else ""),
        title=message.split("\n")[0][:120],
        details=message,
        service=matched,
        thread=thread,
        contact=contact,
    )
    return {"reference": opp.reference, "customer_id": opp.customer_id, "status": "received"}


@tool
def schedule_follow_up(hours_from_now: int = 24, note: str = "", opportunity_reference: str = "",
                       channel: Literal["whatsapp", "instagram", "call", "email", "meeting"] = "whatsapp") -> dict:
    """Schedule a follow-up reminder when the user asks to reconnect later (e.g. 'call me tomorrow' -> 24,
    'next week' -> 168). Links to the current chat's customer or the given opportunity_reference."""
    from datetime import timedelta
    from django.utils import timezone
    from hello_guy_ai.models import Thread

    _require("add", Opportunity)
    ctx = current_context()
    hours = max(1, min(int(hours_from_now), 24 * 90))
    due_at = timezone.now() + timedelta(hours=hours)
    opp = Opportunity.objects.select_related("customer").filter(reference=opportunity_reference.strip()).first() if opportunity_reference.strip() else None
    customer = opp.customer if opp else None
    if customer is None and ctx.thread_id:
        thread = Thread.objects.filter(pk=ctx.thread_id).first()
        contact = services.contact_for_thread(thread) if thread else None
        if contact is not None:
            customer = services.get_or_create_customer(
                name=contact.name or contact.username or contact.phone or "Lead",
                contact=contact,
                source=services.THREAD_SOURCES.get(contact.platform, "manual"),
            )
    if customer is None:
        raise ToolError("Could not identify the customer. Save their inquiry first with create_opportunity.")
    fu = services.schedule_follow_up(customer=customer, opportunity=opp, due_at=due_at, channel=channel, note=note)
    return {"follow_up_id": fu.pk, "due_at": fu.due_at.isoformat(), "customer": customer.name}


@tool
def list_customers(query: str = "", limit: int = 10) -> list[dict]:
    """List customers by name, with how many open opportunities each has. query matches name, company, email,
    phone or Instagram username."""
    _require("view", Customer)
    qs = Customer.objects.all()
    if query:
        qs = qs.filter(Q(name__icontains=query) | Q(company__icontains=query) | Q(email__icontains=query)
                       | Q(phone__icontains=query) | Q(instagram_username__icontains=query))
    qs = qs.annotate(open_opportunities=Count("opportunities", filter=Q(opportunities__status__in=Opportunity.OPEN)))
    return list(qs.values("id", "name", "company", "email", "phone", "instagram_username", "open_opportunities")
                [: _limit(limit)])
