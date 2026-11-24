"""Bring the website's data into the CRM before cms drops its enquiry table (cms 0006):
- each cms.InquirySubmission -> the matching (or a new) Customer + an Opportunity, replies -> its timeline;
- each cms.Solution -> a Service, so opportunities can say what the customer wants."""

from django.db import migrations
from django.db.models import Q

AI_CHAT_TYPE = "AI Chat Assistant"


def import_cms_data(apps, schema_editor):
    Inquiry = apps.get_model("cms", "InquirySubmission")
    Solution = apps.get_model("cms", "Solution")
    Customer = apps.get_model("crm", "Customer")
    Opportunity = apps.get_model("crm", "Opportunity")
    Activity = apps.get_model("crm", "Activity")
    Service = apps.get_model("crm", "Service")

    for solution in Solution.objects.order_by("order"):
        Service.objects.get_or_create(name=solution.title[:150], defaults={
            "description": solution.description, "is_active": solution.is_active})

    for inq in Inquiry.objects.order_by("pk").iterator():
        phone = "" if inq.phone == "Not provided" else inq.phone[:50]
        source = "website_chat" if inq.inquiry_type == AI_CHAT_TYPE else "website_form"
        match = Q(email__iexact=inq.email) | (Q(phone=phone) if phone else Q(pk=None))
        customer = Customer.objects.filter(match).first() or Customer.objects.create(
            name=inq.name[:150], email=inq.email, phone=phone, source=source)
        opp = Opportunity.objects.create(
            reference=inq.inquiry_id,  # visitors may quote it: keep it
            customer=customer,
            title=(inq.subject or inq.inquiry_type or "Website enquiry")[:255],
            details=inq.message[:5000],
            form_type=inq.inquiry_type[:60],
            source=source,
            status="contacted" if inq.is_replied else "new",
        )
        Opportunity.objects.filter(pk=opp.pk).update(created_at=inq.created_at, updated_at=inq.updated_at)
        for reply in inq.replies or []:
            text = reply.get("note") or reply.get("type", "reply") if isinstance(reply, dict) else str(reply)
            by = reply.get("by", "") if isinstance(reply, dict) else ""
            Activity.objects.create(customer=customer, opportunity=opp, kind="email",
                                    body=f"{text}{f' (by {by})' if by else ''}"[:5000])


def export_cms_data(apps, schema_editor):
    Inquiry = apps.get_model("cms", "InquirySubmission")
    Opportunity = apps.get_model("crm", "Opportunity")
    for opp in Opportunity.objects.filter(source__in=["website_form", "website_chat"]).select_related("customer"):
        Inquiry.objects.update_or_create(inquiry_id=opp.reference, defaults=dict(
            name=opp.customer.name, email=opp.customer.email or "unknown@example.com",
            phone=opp.customer.phone or "Not provided", subject=opp.title, message=opp.details,
            inquiry_type=opp.form_type or AI_CHAT_TYPE, is_replied=opp.status != "new",
        ))


class Migration(migrations.Migration):
    dependencies = [
        ("crm", "0001_initial"),
        ("cms", "0005_sitesettings_phone_no_placeholder"),
    ]

    operations = [migrations.RunPython(import_cms_data, export_cms_data)]
