"""CRM use cases. Views, the website form, AI tools and the dashboard all go through these functions.

    from crm.services import create_opportunity
    create_opportunity(name="Asha", email="asha@example.com", title="Website redesign", source=Source.WEBSITE_FORM)
"""

from django.db import transaction
from django.db.models import Q
from django.urls import reverse

from crm.models import Activity, Customer, Opportunity, Source
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


def match_customer(*, email: str = "", phone: str = "", contact=None) -> Customer | None:
    """The existing customer with this email, phone or inbox contact."""
    query = Q()
    if email.strip():
        query |= Q(email__iexact=email.strip())
    if phone.strip():
        query |= Q(phone=phone.strip())
    if contact is not None:
        query |= Q(contacts=contact)
    return Customer.objects.filter(query).distinct().first() if query else None


@transaction.atomic
def get_or_create_customer(*, name: str, email: str = "", phone: str = "", company: str = "", contact=None,
                           source: str = Source.MANUAL, created_by=None) -> Customer:
    """One person = one customer across channels: reuse the customer with the same email, phone or inbox
    contact (filling in details they lacked), else create one. Links the inbox contact for messaging."""
    details = {"email": email.strip(), "phone": phone.strip()[:50], "company": company.strip()[:150]}
    customer = match_customer(email=email, phone=phone, contact=contact)
    if customer is None:
        customer = Customer.objects.create(name=name.strip()[:150] or "Unknown", source=source, **details)
        Activity.objects.create(customer=customer, kind=Activity.Kind.SYSTEM, created_by=created_by,
                                body=f"Customer added ({customer.get_source_display()})")
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
        customer = get_or_create_customer(name=name, email=email, phone=phone, company=company, contact=contact,
                                          source=source, created_by=created_by)
    opportunity = Opportunity.objects.create(
        customer=customer, service=service, title=title.strip()[:255] or "Enquiry", details=details.strip()[:5000],
        source=source, form_type=form_type[:60], thread=thread,
    )
    Activity.objects.create(customer=customer, opportunity=opportunity, kind=Activity.Kind.SYSTEM,
                            created_by=created_by,
                            body=f"New opportunity: {opportunity.title} ({opportunity.get_source_display()})")
    transaction.on_commit(lambda: notify_team(f"New opportunity: {customer.name}", opportunity.title,
                                              reverse("crm:opportunity", args=[opportunity.pk])))
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
