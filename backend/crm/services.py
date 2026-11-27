"""CRM use cases. Views, the website form, AI tools and the dashboard all go through these functions.

    from crm.services import create_opportunity
    create_opportunity(name="Asha", email="asha@example.com", title="Website redesign", source=Source.WEBSITE_FORM)
"""

from datetime import datetime, time
from decimal import Decimal
import logging

from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from core import follow_ups as global_followups, notifications as global_notifications
from core.models import FollowUp
from crm.models import Activity, Customer, Opportunity, PaymentMilestone, Proposal, Source
from meta_connector.models import Contact
from meta_connector.roles import notify_team

logger = logging.getLogger(__name__)

# hello_guy_ai thread external_id prefixes -> source ("<platform>:<account id>:<user id>", "web:<random>")
THREAD_SOURCES = {
    "web": Source.WEBSITE_CHAT,
    "whatsapp": Source.WHATSAPP,
    "instagram": Source.INSTAGRAM,
    "facebook": Source.FACEBOOK,
}


def contact_for_thread(thread) -> Contact | None:
    """The WhatsApp/Instagram contact a DM thread belongs to (None for website and staff threads)."""
    parts = (thread.external_id or "").split(":", 2)
    if len(parts) != 3 or parts[0] == "web":
        return None
    platform, account_id, user_id = parts
    return Contact.objects.filter(
        platform_account_id=account_id, platform_account__platform=platform, platform_user_id=user_id
    ).first()


def _digits(phone: str) -> str:
    return "".join(ch for ch in phone if ch.isdigit())


def _clean_phone(phone) -> str:
    """The trimmed phone, or "" when it holds no digits (e.g. "N/A")."""
    phone = (phone or "").strip()
    return phone if _digits(phone) else ""


def _same_phone(a: str, b: str) -> bool:
    """Same digits, or the same number with and without a country code (+91 98765 43210 = 9876543210)."""
    a, b = _digits(a), _digits(b)
    if not a or not b:
        return False
    short, long_ = sorted((a, b), key=len)
    return short == long_ or (len(short) >= 10 and len(long_) - len(short) <= 3 and long_.endswith(short))


def match_customer(*, email: str = "", phone: str = "", instagram_username: str = "", contact=None) -> Customer | None:
    """The existing customer with this email, Instagram handle or inbox contact, else with this phone number
    (never when both have an email and the emails differ: that is a different person on a shared number)."""
    emails = {e.strip().lower() for e in (email, getattr(contact, "email", "")) if e and e.strip()}
    phones = [p for p in (_clean_phone(phone), _clean_phone(getattr(contact, "phone", ""))) if p]
    query = Q()
    for e in emails:
        query |= Q(email__iexact=e)
    ig = instagram_username.strip().lstrip("@")
    if ig:
        query |= Q(instagram_username__iexact=ig)
    if contact is not None:
        query |= Q(contacts=contact)
        if getattr(contact, "username", ""):
            query |= Q(instagram_username__iexact=contact.username.strip().lstrip("@"))
    if query and (found := Customer.objects.filter(query).distinct().first()):
        return found
    if phones:
        # shortcut: compares in Python because phones are stored free-form; index a digits column if customers reach 100k+
        for pk, stored_phone, stored_email in Customer.objects.exclude(phone="").values_list("pk", "phone", "email"):
            if any(_same_phone(p, stored_phone) for p in phones) and not (emails and stored_email.strip()):
                return Customer.objects.get(pk=pk)
    return None


@transaction.atomic
def get_or_create_customer(*, name: str, email: str = "", phone: str = "", instagram_username: str = "",
                           company: str = "", notes: str = "", contact=None, source: str = Source.MANUAL, created_by=None) -> Customer:
    """One person = one customer across channels: reuse the customer with the same email, phone, Instagram handle
    or inbox contact (filling in details they lacked), else create one. Links the inbox contact for messaging."""
    ig = (instagram_username or (getattr(contact, "username", "") if contact else "")).strip().lstrip("@")[:100]
    resolved_email = (email or "").strip() or (getattr(contact, "email", "") or "").strip()
    resolved_phone = (_clean_phone(phone) or _clean_phone(getattr(contact, "phone", "")))[:50]
    details = {
        "email": resolved_email,
        "phone": resolved_phone,
        "instagram_username": ig,
        "company": company.strip()[:150],
        "notes": notes.strip()[:2000],
    }
    customer = match_customer(email=resolved_email, phone=resolved_phone, instagram_username=ig, contact=contact)
    if customer is None:
        customer = Customer.objects.create(name=name.strip()[:150] or "Unknown", source=source, **details)
        Activity.objects.create(
            customer=customer,
            kind=Activity.Kind.SYSTEM,
            created_by=created_by,
            body=f"Customer created from {customer.get_source_display()}" + (f": {notes.strip()[:300]}" if notes.strip() else ""),
        )
        def _notify_new_customer():
            try:
                from core import notifications as global_notifications
                global_notifications.notify_staff(
                    title=f"New customer from {customer.get_source_display()}: {customer.name}",
                    body=notes.strip()[:160] or resolved_email or resolved_phone,
                    url=reverse("crm:customer", args=[customer.pk]),
                    category="crm",
                    level="success",
                )
            except Exception:
                logger.exception("Could not notify staff about customer %s", customer.pk)
        transaction.on_commit(_notify_new_customer)
    elif missing := [field for field, value in details.items() if value and not getattr(customer, field)]:
        for field in missing:
            setattr(customer, field, details[field])
        customer.save(update_fields=[*missing, "updated_at"])
    if contact is not None:
        customer.contacts.add(contact)
    return customer


def default_staff_assignee():
    """Primary active staff user for unassigned incoming leads and follow-ups."""
    from django.contrib.auth import get_user_model

    return get_user_model().objects.filter(is_active=True, is_staff=True).order_by("-is_superuser", "id").first()


@transaction.atomic
def create_opportunity(*, title: str, customer: Customer | None = None, name: str = "", email: str = "",
                       phone: str = "", company: str = "", details: str = "", service=None, services_list=None,
                       source: str = Source.MANUAL, form_type: str = "", thread=None, contact=None,
                       created_by=None, default_followup: bool = True) -> Opportunity:
    """Record what a customer is interested in and alert the team.

    Pass `customer`, or the person's details (name, email, phone, or the chat `thread`) to find or create them.
    """
    if thread is not None:
        source = THREAD_SOURCES.get((thread.external_id or "").split(":", 1)[0], source)
        contact = contact or contact_for_thread(thread)
    if customer is None:
        customer = get_or_create_customer(name=name, email=email, phone=phone, company=company, notes=details,
                                          contact=contact, source=source, created_by=created_by)
    extra_services = list(services_list or [])
    primary_service = service or (extra_services[0] if extra_services else None)
    opportunity = Opportunity.objects.create(
        customer=customer, service=primary_service, title=title.strip()[:255] or "Enquiry",
        details=details.strip()[:5000], source=source, form_type=form_type[:60], thread=thread,
    )
    if primary_service or extra_services:
        opportunity.services.set({*( [primary_service] if primary_service else [] ), *extra_services})
    Activity.objects.create(customer=customer, opportunity=opportunity, kind=Activity.Kind.SYSTEM,
                            created_by=created_by,
                            body=f"New opportunity: {opportunity.title} ({opportunity.get_source_display()})")
    transaction.on_commit(lambda: notify_team(f"New opportunity: {customer.name}", opportunity.title,
                                              reverse("crm:opportunity", args=[opportunity.pk])))
    if default_followup:
        try:
            assignee = opportunity.assigned_to or default_staff_assignee()
            if assignee:
                with transaction.atomic():  # savepoint: a failing follow-up must not poison the opportunity's transaction
                    global_followups.schedule_follow_up(
                        title=f"Follow up: {customer.name} ({opportunity.title[:60]})",
                        due_at=timezone.now() + timezone.timedelta(hours=24),
                        channel="Email" if customer.email else "WhatsApp",
                        note=opportunity.details[:300],
                        assigned_to=assignee,
                        created_by=created_by,
                        customer=customer,
                        opportunity=opportunity,
                        related_label=customer.name,
                        related_url=reverse("crm:opportunity", args=[opportunity.pk]),
                        notify_now=True,
                    )
        except Exception:
            logger.exception("Could not schedule the follow-up for opportunity %s", opportunity.pk)
    return opportunity


def set_status(
    opportunity: Opportunity,
    status: str,
    user=None,
    note: str = "",
    lost_reason: str = "",
    won_value: Decimal | None = None,
) -> Opportunity:
    old = opportunity.get_status_display()
    opportunity.status = status
    fields = ["status", "updated_at"]
    if status == Opportunity.Status.LOST and lost_reason:
        opportunity.lost_reason = lost_reason.strip()[:120]
        fields.append("lost_reason")
    if status == Opportunity.Status.WON:
        if won_value is not None:
            opportunity.won_value = won_value
            fields.append("won_value")
        elif opportunity.won_value is None and opportunity.estimated_value is not None:
            opportunity.won_value = opportunity.estimated_value
            fields.append("won_value")
    opportunity.save(update_fields=fields)
    reason_txt = f" (Reason: {opportunity.lost_reason})" if status == Opportunity.Status.LOST and opportunity.lost_reason else ""
    body = f"{opportunity.title}: {old} → {opportunity.get_status_display()}{reason_txt}" + (f"\n{note}" if note else "")
    add_note(user=user, customer=opportunity.customer, opportunity=opportunity, kind=Activity.Kind.STATUS, body=body)
    return opportunity


@transaction.atomic
def convert_opportunity_to_project(opportunity: Opportunity, user=None):
    """Create a linked PMS delivery Project (with starter tasks) from a CRM Opportunity."""
    from pms.models import Project, Task

    existing = opportunity.pms_projects.order_by("pk").first()
    if existing is not None:
        return existing

    owner = opportunity.assigned_to or opportunity.customer.owner or (
        user if getattr(user, "is_authenticated", False) else None
    )
    project = Project.objects.create(
        name=opportunity.title[:200],
        description=opportunity.details or f"Delivery project for {opportunity.customer.name}",
        customer=opportunity.customer,
        opportunity=opportunity,
        owner=owner,
        budget=opportunity.effective_value or None,
        start_date=timezone.localdate(),
    )
    if owner:
        project.members.add(owner)

    starter_titles = [
        ("Kickoff & Requirements", Task.Priority.HIGH),
        ("Architecture & Design", Task.Priority.MEDIUM),
        ("Implementation & Testing", Task.Priority.MEDIUM),
        ("Client Handover & Launch", Task.Priority.MEDIUM),
    ]
    for title, prio in starter_titles:
        Task.objects.create(project=project, title=title, priority=prio, assignee=owner)

    add_note(
        user=user if getattr(user, "is_authenticated", False) else None,
        customer=opportunity.customer,
        opportunity=opportunity,
        kind=Activity.Kind.SYSTEM,
        body=f"Converted deal to PMS Project: {project.name} (#{project.pk})",
    )
    if owner:
        global_notifications.notify(
            user=owner,
            title=f"New delivery project: {project.name}",
            body=f"Created from {opportunity.customer.name} ({opportunity.reference})",
            url=reverse("pms:project", args=[project.pk]),
            category="crm",
            level="success",
        )
    return project


@transaction.atomic
def add_proposal(
    *,
    opportunity: Opportunity,
    title: str,
    amount: Decimal,
    currency: str = "",
    valid_until=None,
    document_url: str = "",
    notes: str = "",
    mark_sent: bool = False,
    user=None,
) -> Proposal:
    prop = Proposal.objects.create(
        opportunity=opportunity,
        title=title.strip()[:200],
        amount=amount,
        currency=currency or opportunity.currency,
        status=Proposal.Status.SENT if mark_sent else Proposal.Status.DRAFT,
        valid_until=valid_until,
        document_url=document_url.strip()[:500],
        notes=notes.strip()[:2000],
        sent_at=timezone.now() if mark_sent else None,
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )
    if not opportunity.estimated_value:
        opportunity.estimated_value = amount
        opportunity.currency = prop.currency
        opportunity.save(update_fields=["estimated_value", "currency", "updated_at"])
    if mark_sent and opportunity.status in (Opportunity.Status.NEW, Opportunity.Status.CONTACTED, Opportunity.Status.QUALIFIED):
        set_status(opportunity, Opportunity.Status.PROPOSAL, user=user, note=f"Proposal sent: {prop.title}")
    else:
        add_note(
            user=user,
            customer=opportunity.customer,
            opportunity=opportunity,
            kind=Activity.Kind.NOTE,
            body=f"Proposal created ({prop.get_status_display()}): {prop.title} — {prop.currency_symbol}{prop.amount}",
        )
    return prop


@transaction.atomic
def update_proposal_status(proposal: Proposal, status: str, user=None) -> Proposal:
    proposal.status = status
    fields = ["status", "updated_at"]
    now = timezone.now()
    if status == Proposal.Status.SENT and proposal.sent_at is None:
        proposal.sent_at = now
        fields.append("sent_at")
    elif status in (Proposal.Status.ACCEPTED, Proposal.Status.REJECTED):
        proposal.responded_at = now
        fields.append("responded_at")
    proposal.save(update_fields=fields)

    opp = proposal.opportunity
    if status == Proposal.Status.SENT and opp.status in (Opportunity.Status.NEW, Opportunity.Status.CONTACTED, Opportunity.Status.QUALIFIED):
        set_status(opp, Opportunity.Status.PROPOSAL, user=user, note=f"Proposal sent: {proposal.title}")
    elif status == Proposal.Status.ACCEPTED:
        set_status(opp, Opportunity.Status.WON, user=user, note=f"Proposal accepted: {proposal.title}", won_value=proposal.amount)
    elif status == Proposal.Status.REJECTED:
        add_note(user=user, customer=opp.customer, opportunity=opp, kind=Activity.Kind.STATUS, body=f"Proposal rejected: {proposal.title}")
    return proposal


@transaction.atomic
def add_payment_milestone(
    *,
    opportunity: Opportunity,
    title: str,
    amount: Decimal,
    currency: str = "",
    due_date=None,
    invoice_number: str = "",
    status: str = PaymentMilestone.Status.PENDING,
    user=None,
) -> PaymentMilestone:
    ms = PaymentMilestone.objects.create(
        opportunity=opportunity,
        title=title.strip()[:150],
        amount=amount,
        currency=currency or opportunity.currency,
        due_date=due_date,
        invoice_number=invoice_number.strip()[:60],
        status=status,
        paid_at=timezone.now() if status == PaymentMilestone.Status.PAID else None,
        created_by=user if getattr(user, "is_authenticated", False) else None,
    )
    add_note(
        user=user,
        customer=opportunity.customer,
        opportunity=opportunity,
        kind=Activity.Kind.SYSTEM,
        body=f"Payment milestone added: {ms.title} — {ms.currency_symbol}{ms.amount}",
    )
    if due_date and ms.status in (PaymentMilestone.Status.PENDING, PaymentMilestone.Status.INVOICED):
        tz = timezone.get_current_timezone()
        due_dt = datetime.combine(due_date, time(10, 0), tzinfo=tz)
        schedule_follow_up(
            customer=opportunity.customer,
            opportunity=opportunity,
            due_at=due_dt,
            channel="Payment / Invoice",
            title=f"Payment due: {ms.title} ({ms.currency_symbol}{ms.amount})",
            note=f"Invoice {ms.invoice_number}".strip() if ms.invoice_number else f"Follow up on {ms.title}",
            created_by=user,
        )
    return ms


@transaction.atomic
def update_milestone_status(milestone: PaymentMilestone, status: str, user=None) -> PaymentMilestone:
    milestone.status = status
    fields = ["status", "updated_at"]
    if status == PaymentMilestone.Status.PAID:
        milestone.paid_at = timezone.now()
        fields.append("paid_at")
    milestone.save(update_fields=fields)
    opp = milestone.opportunity
    add_note(
        user=user,
        customer=opp.customer,
        opportunity=opp,
        kind=Activity.Kind.SYSTEM,
        body=f"Payment milestone {milestone.get_status_display()}: {milestone.title} ({milestone.currency_symbol}{milestone.amount})",
    )
    return milestone


def add_note(*, user, body: str, customer: Customer, opportunity: Opportunity | None = None,
             kind: str = Activity.Kind.NOTE) -> Activity:
    return Activity.objects.create(customer=customer, opportunity=opportunity, kind=kind, body=body.strip()[:5000],
                                   created_by=user)


@transaction.atomic
def schedule_follow_up(*, customer: Customer, due_at, note: str = "", opportunity: Opportunity | None = None,
                       channel: str = "WhatsApp", assigned_to=None, created_by=None, title: str = "",
                       priority: str = FollowUp.Priority.NORMAL) -> FollowUp:
    """Schedule a unified follow-up linked to the CRM customer/opportunity and log it on the timeline."""
    assignee = assigned_to or (opportunity.assigned_to if opportunity else customer.owner) or default_staff_assignee()
    follow_up = global_followups.schedule_follow_up(
        title=(title or note).strip()[:200] or f"Follow up with {customer.name}",
        due_at=due_at,
        channel=channel or "WhatsApp",
        note=note.strip()[:500],
        priority=priority,
        assigned_to=assignee,
        created_by=created_by,
        customer=customer,
        opportunity=opportunity,
        related_label=customer.name,
        related_url=reverse("crm:opportunity", args=[opportunity.pk]) if opportunity else reverse("crm:customer", args=[customer.pk]),
        notify_now=bool(assignee or created_by),
    )
    Activity.objects.create(
        customer=customer,
        opportunity=opportunity,
        kind=Activity.Kind.SYSTEM,
        created_by=created_by,
        body=f"Follow-up scheduled for {timezone.localtime(due_at):%d %b %Y, %I:%M %p}" + (f": {note}" if note else ""),
    )
    return follow_up


@transaction.atomic
def complete_follow_up(follow_up: FollowUp, user=None) -> FollowUp:
    """Mark a follow-up as done, sync Opportunity.next_follow_up, and log on the CRM timeline."""
    return global_followups.complete_follow_up(follow_up, user=user)
