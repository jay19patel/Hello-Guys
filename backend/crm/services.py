"""CRM use cases. Views, the website form, AI tools and the dashboard all go through these functions.

    from crm.services import create_opportunity
    create_opportunity(name="Asha", email="asha@example.com", title="Website redesign", source=Source.WEBSITE_FORM)
"""

from django.db import transaction
from django.db.models import Q
from django.urls import reverse
from django.utils import timezone

from crm.models import Activity, Customer, FollowUp, Opportunity, Source
from meta_connector.models import Contact
from meta_connector.roles import notify_team

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


def _phone_variants(phone: str) -> list[str]:
    cleaned = phone.strip()
    if not cleaned:
        return []
    digits = "".join(ch for ch in cleaned if ch.isdigit())
    variants = [cleaned]
    if digits:
        variants.append(digits)
        variants.append(f"+{digits}")
        if len(digits) >= 10:
            variants.append(digits[-10:])
    return list(dict.fromkeys(variants))


def match_customer(*, email: str = "", phone: str = "", instagram_username: str = "", contact=None) -> Customer | None:
    """The existing customer with this email, phone, Instagram handle or inbox contact."""
    query = Q()
    if email.strip():
        query |= Q(email__iexact=email.strip())
    for p in _phone_variants(phone):
        query |= Q(phone__iendswith=p) if len(p) == 10 and p.isdigit() else Q(phone=p)
    ig = instagram_username.strip().lstrip("@")
    if ig:
        query |= Q(instagram_username__iexact=ig)
    if contact is not None:
        query |= Q(contacts=contact)
        if getattr(contact, "email", ""):
            query |= Q(email__iexact=contact.email.strip())
        if getattr(contact, "phone", ""):
            for p in _phone_variants(contact.phone):
                query |= Q(phone__iendswith=p) if len(p) == 10 and p.isdigit() else Q(phone=p)
        if getattr(contact, "username", ""):
            query |= Q(instagram_username__iexact=contact.username.strip().lstrip("@"))
    return Customer.objects.filter(query).distinct().first() if query else None


@transaction.atomic
def get_or_create_customer(*, name: str, email: str = "", phone: str = "", instagram_username: str = "",
                           company: str = "", notes: str = "", contact=None, source: str = Source.MANUAL, created_by=None) -> Customer:
    """One person = one customer across channels: reuse the customer with the same email, phone, Instagram handle
    or inbox contact (filling in details they lacked), else create one. Links the inbox contact for messaging."""
    ig = (instagram_username or (getattr(contact, "username", "") if contact else "")).strip().lstrip("@")[:100]
    resolved_email = (email or (getattr(contact, "email", "") if contact else "")).strip()
    resolved_phone = (phone or (getattr(contact, "phone", "") if contact else "")).strip()[:50]
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
                pass
        transaction.on_commit(_notify_new_customer)
    elif missing := [field for field, value in details.items() if value and not getattr(customer, field)]:
        for field in missing:
            setattr(customer, field, details[field])
        customer.save(update_fields=[*missing, "updated_at"])
    if contact is not None:
        customer.contacts.add(contact)
    return customer


@transaction.atomic
def create_opportunity(*, title: str, customer: Customer | None = None, name: str = "", email: str = "",
                       phone: str = "", company: str = "", details: str = "", service=None,
                       source: str = Source.MANUAL, form_type: str = "", thread=None, contact=None,
                       created_by=None) -> Opportunity:
    """Record what a customer is interested in and alert the team.

    Pass `customer`, or the person's details (name, email, phone, or the chat `thread`) to find or create them.
    """
    if thread is not None:
        source = THREAD_SOURCES.get((thread.external_id or "").split(":", 1)[0], source)
        contact = contact or contact_for_thread(thread)
    if customer is None:
        customer = get_or_create_customer(name=name, email=email, phone=phone, company=company, notes=details,
                                          contact=contact, source=source, created_by=created_by)
    opportunity = Opportunity.objects.create(
        customer=customer, service=service, title=title.strip()[:255] or "Enquiry", details=details.strip()[:5000],
        source=source, form_type=form_type[:60], thread=thread,
    )
    Activity.objects.create(customer=customer, opportunity=opportunity, kind=Activity.Kind.SYSTEM,
                            created_by=created_by,
                            body=f"New opportunity: {opportunity.title} ({opportunity.get_source_display()})")
    transaction.on_commit(lambda: notify_team(f"New opportunity: {customer.name}", opportunity.title,
                                              reverse("crm:opportunity", args=[opportunity.pk])))
    try:
        from core import reminders as global_reminders
        from django.contrib.auth import get_user_model
        assignee = opportunity.assigned_to or get_user_model().objects.filter(is_active=True, is_staff=True).order_by("-is_superuser", "id").first()
        if assignee:
            global_reminders.schedule_follow_up(
                title=f"Follow up: {customer.name} ({opportunity.title[:60]})",
                due_at=timezone.now() + timezone.timedelta(hours=24),
                channel="Email" if customer.email else "WhatsApp",
                note=opportunity.details[:300],
                assigned_to=assignee,
                created_by=created_by,
                related_label=customer.name,
                related_url=reverse("crm:opportunity", args=[opportunity.pk]),
                entity_type="opportunity",
                entity_id=str(opportunity.pk),
                notify_now=True,
            )
    except Exception:
        pass
    return opportunity


def set_status(opportunity: Opportunity, status: str, user=None, note: str = "") -> Opportunity:
    old = opportunity.get_status_display()
    opportunity.status = status
    opportunity.save(update_fields=["status", "updated_at"])
    body = f"{opportunity.title}: {old} → {opportunity.get_status_display()}" + (f"\n{note}" if note else "")
    add_note(user=user, customer=opportunity.customer, opportunity=opportunity, kind=Activity.Kind.STATUS, body=body)
    return opportunity


def add_note(*, user, body: str, customer: Customer, opportunity: Opportunity | None = None,
             kind: str = Activity.Kind.NOTE) -> Activity:
    return Activity.objects.create(customer=customer, opportunity=opportunity, kind=kind, body=body.strip()[:5000],
                                   created_by=user)


@transaction.atomic
def schedule_follow_up(*, customer: Customer, due_at, note: str = "", opportunity: Opportunity | None = None,
                       channel: str = FollowUp.Channel.WHATSAPP, assigned_to=None, created_by=None) -> FollowUp:
    """Schedule a follow-up reminder and keep Opportunity.next_follow_up synced."""
    follow_up = FollowUp.objects.create(
        customer=customer,
        opportunity=opportunity,
        assigned_to=assigned_to or (opportunity.assigned_to if opportunity else customer.owner),
        due_at=due_at,
        channel=channel,
        note=note.strip()[:500],
        created_by=created_by,
    )
    if opportunity is not None and (opportunity.next_follow_up is None or due_at < opportunity.next_follow_up):
        opportunity.next_follow_up = due_at
        opportunity.save(update_fields=["next_follow_up", "updated_at"])
    Activity.objects.create(
        customer=customer,
        opportunity=opportunity,
        kind=Activity.Kind.SYSTEM,
        created_by=created_by,
        body=f"Follow-up scheduled for {timezone.localtime(due_at):%d %b %Y, %H:%M}" + (f": {note}" if note else ""),
    )
    try:
        from core import reminders as global_followups
        global_followups.schedule_follow_up(
            title=note.strip()[:200] or f"Follow up with {customer.name}",
            due_at=due_at,
            channel=follow_up.get_channel_display(),
            note=note,
            assigned_to=follow_up.assigned_to,
            created_by=created_by,
            related_label=customer.name,
            related_url=reverse("crm:customer", args=[customer.pk]),
            entity_type="customer",
            entity_id=str(customer.pk),
            notify_now=bool(follow_up.assigned_to or created_by),
        )
    except Exception:
        pass
    return follow_up


@transaction.atomic
def complete_follow_up(follow_up: FollowUp, user=None) -> FollowUp:
    """Mark a follow-up as done and update the linked opportunity's next_follow_up."""
    follow_up.status = FollowUp.Status.DONE
    follow_up.completed_at = timezone.now()
    follow_up.save(update_fields=["status", "completed_at", "updated_at"])
    if follow_up.opportunity is not None:
        next_pending = follow_up.opportunity.follow_ups.filter(status=FollowUp.Status.PENDING).order_by("due_at").first()
        follow_up.opportunity.next_follow_up = next_pending.due_at if next_pending else None
        follow_up.opportunity.save(update_fields=["next_follow_up", "updated_at"])
    Activity.objects.create(
        customer=follow_up.customer,
        opportunity=follow_up.opportunity,
        kind=Activity.Kind.SYSTEM,
        created_by=user,
        body=f"Completed follow-up ({follow_up.get_channel_display()}): {follow_up.note or 'No note'}",
    )
    return follow_up
