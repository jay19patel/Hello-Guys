"""Demo customers with opportunities, notes and a meeting, to try the CRM. Safe to run again (matched by email)."""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from crm.models import Activity, Customer, Meeting, Opportunity, Service, Source

S = Opportunity.Status
CUSTOMERS = [
    {"name": "Ravi Sharma", "company": "Sharma Textiles", "email": "ravi@sharmatextiles.example", "phone": "+919812345601",
     "source": Source.WHATSAPP, "opportunities": [
        ("ERPNext for 3 factories", "ERPNext Implementation", S.PROPOSAL, "Inventory + manufacturing, 40 users. Budget ~8L."),
        ("WhatsApp order bot", "Website & Support Chatbots (RAG)", S.NEW, "Dealers should place orders on WhatsApp."),
     ]},
    {"name": "Asha Mehta", "company": "Mehta Clinic", "email": "asha@mehtaclinic.example", "phone": "+919812345602",
     "instagram_username": "mehtaclinic", "source": Source.INSTAGRAM, "opportunities": [
        ("Appointment chatbot", "Website & Support Chatbots (RAG)", S.QUALIFIED, "Book, reschedule and FAQs on Instagram."),
     ]},
    {"name": "Karan Patel", "company": "Patel Logistics", "email": "karan@patellogistics.example",
     "source": Source.WEBSITE_FORM, "opportunities": [
        ("Annual support contract", "ERPNext Support & AMC", S.WON, "Existing ERPNext v14, needs AMC from April."),
        ("Fleet tracking app", "Custom Frappe Apps", S.CONTACTED, "Driver app + dispatch dashboard."),
     ]},
    {"name": "Neha Gupta", "company": "", "email": "neha.gupta@example.com", "phone": "+919812345604",
     "source": Source.WEBSITE_CHAT, "opportunities": [
        ("Website redesign", "", S.LOST, "Chose a cheaper freelancer."),
     ]},
    {"name": "Vikram Singh", "company": "Singh Hardware", "email": "vikram@singhhardware.example",
     "source": Source.MANUAL, "opportunities": [
        ("AI assistant for sales team", "AI Assistants for ERPNext", S.NEW, "Wants quotes drafted from ERPNext data."),
     ]},
]


class Command(BaseCommand):
    help = "Create demo CRM customers, opportunities, notes and a meeting"

    @transaction.atomic
    def handle(self, *args, **options):
        now = timezone.now()
        for i, row in enumerate(CUSTOMERS):
            row = dict(row)
            opportunities = row.pop("opportunities")
            customer, created = Customer.objects.get_or_create(email=row["email"], defaults=row)
            if not created:
                continue
            Activity.objects.create(customer=customer, kind=Activity.Kind.SYSTEM,
                                    body=f"Customer added ({customer.get_source_display()})")
            for title, service, status, details in opportunities:
                opp = Opportunity.objects.create(
                    customer=customer, title=title, details=details, status=status, source=customer.source,
                    service=Service.objects.filter(name=service).first() if service else None,
                    next_follow_up=now + timedelta(days=i) if status in Opportunity.OPEN else None,
                )
                Activity.objects.create(customer=customer, opportunity=opp, kind=Activity.Kind.CALL,
                                        body=f"Discussed: {title}. {details}")
            if i == 0:
                start = (now + timedelta(days=2)).replace(hour=11, minute=0, second=0, microsecond=0)
                Meeting.objects.create(title="ERPNext demo", customer=customer, opportunity=customer.opportunities.first(),
                                       start_at=start, end_at=start + timedelta(hours=1), attendees=[customer.email])
        self.stdout.write(self.style.SUCCESS(
            f"{Customer.objects.count()} customers, {Opportunity.objects.count()} opportunities in the CRM."))
