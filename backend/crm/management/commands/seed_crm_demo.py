"""Demo data across Meta Inbox (WhatsApp + Instagram), AI Approval Queue, CRM Customers,
Opportunities, Follow-ups and Meetings. Safe to run again (idempotent)."""

from datetime import timedelta

from django.core.management.base import BaseCommand
from django.db import transaction
from django.utils import timezone

from crm.models import Activity, Customer, FollowUp, Meeting, Opportunity, Service, Source
from meta_connector.models import Contact, Message, PendingAIReply, PlatformAccount

S = Opportunity.Status
SERVICES = [
    "ERPNext Implementation",
    "Website & Support Chatbots (RAG)",
    "ERPNext Support & AMC",
    "Custom Frappe Apps",
    "AI Assistants for ERPNext",
]

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
    {"name": "Vikram Singh", "company": "Singh Hardware", "email": "vikram@singhhardware.example", "phone": "+919812345605",
     "source": Source.MANUAL, "opportunities": [
        ("AI assistant for sales team", "AI Assistants for ERPNext", S.NEW, "Wants quotes drafted from ERPNext data."),
     ]},
]


class Command(BaseCommand):
    help = "Create demo CRM & Meta Inbox data (customers, opportunities, follow-ups, contacts, messages, AI approvals)"

    @transaction.atomic
    def handle(self, *args, **options):
        now = timezone.now()

        for svc_name in SERVICES:
            Service.objects.get_or_create(name=svc_name)

        for i, row in enumerate(CUSTOMERS):
            row = dict(row)
            opportunities = row.pop("opportunities")
            customer, created = Customer.objects.get_or_create(email=row["email"], defaults=row)
            if created:
                Activity.objects.create(customer=customer, kind=Activity.Kind.SYSTEM,
                                        body=f"Customer added ({customer.get_source_display()})")
                for title, service, status, details in opportunities:
                    opp = Opportunity.objects.create(
                        customer=customer, title=title, details=details, status=status, source=customer.source,
                        service=Service.objects.filter(name=service).first() if service else None,
                        next_follow_up=now + timedelta(days=i + 1) if status in Opportunity.OPEN else None,
                    )
                    Activity.objects.create(customer=customer, opportunity=opp, kind=Activity.Kind.CALL,
                                            body=f"Discussed: {title}. {details}")
                if i == 0:
                    start = (now + timedelta(days=2)).replace(hour=11, minute=0, second=0, microsecond=0)
                    Meeting.objects.create(title="ERPNext demo", customer=customer, opportunity=customer.opportunities.first(),
                                           start_at=start, end_at=start + timedelta(hours=1), attendees=[customer.email])

        # Meta Connector Accounts
        wa_acc, _ = PlatformAccount.objects.get_or_create(
            platform=PlatformAccount.PLATFORM_WHATSAPP,
            phone_number_id="demo_wa_1001",
            defaults={"display_name": "Sales WhatsApp", "is_active": True, "ai_auto_reply": True, "ai_require_approval": False},
        )
        ig_acc, _ = PlatformAccount.objects.get_or_create(
            platform=PlatformAccount.PLATFORM_INSTAGRAM,
            instagram_id="demo_ig_2001",
            defaults={"display_name": "NJTechStudio IG", "is_active": True, "ai_auto_reply": True, "ai_require_approval": True},
        )

        # Contacts + Cross-channel linking
        ravi_wa, _ = Contact.objects.get_or_create(
            platform_account=wa_acc, platform_user_id="919812345601",
            defaults={"name": "Ravi Sharma", "phone": "919812345601", "email": "ravi@sharmatextiles.example", "last_message_at": now - timedelta(minutes=18)},
        )
        asha_ig, _ = Contact.objects.get_or_create(
            platform_account=ig_acc, platform_user_id="ig_asha_202",
            defaults={"name": "Asha Mehta", "username": "mehtaclinic", "email": "asha@mehtaclinic.example", "last_message_at": now - timedelta(minutes=5)},
        )
        asha_wa, _ = Contact.objects.get_or_create(
            platform_account=wa_acc, platform_user_id="919812345602",
            defaults={"name": "Asha Mehta", "phone": "919812345602", "email": "asha@mehtaclinic.example", "last_message_at": now - timedelta(hours=2)},
        )
        vikram_wa, _ = Contact.objects.get_or_create(
            platform_account=wa_acc, platform_user_id="919812345605",
            defaults={"name": "Vikram Singh", "phone": "919812345605", "last_message_at": now - timedelta(days=1)},
        )

        ravi_cust = Customer.objects.filter(email="ravi@sharmatextiles.example").first()
        asha_cust = Customer.objects.filter(email="asha@mehtaclinic.example").first()
        vikram_cust = Customer.objects.filter(email="vikram@singhhardware.example").first()
        if ravi_cust:
            ravi_cust.contacts.add(ravi_wa)
        if asha_cust:
            # Cross-channel: same Customer linked to BOTH Instagram and WhatsApp!
            asha_cust.contacts.add(asha_ig, asha_wa)
        if vikram_cust:
            vikram_cust.contacts.add(vikram_wa)

        # Sample Messages across days for charts & conversations
        sample_msgs = [
            ("demo_m1", wa_acc, ravi_wa, Message.DIRECTION_INCOMING, "Hi, we need ERPNext for 3 textile factories.", 3, 2),
            ("demo_m2", wa_acc, ravi_wa, Message.DIRECTION_OUTGOING, "Sure Ravi! We can help with inventory and manufacturing. Would you like a demo?", 3, 1),
            ("demo_m3", wa_acc, ravi_wa, Message.DIRECTION_INCOMING, "Yes please, also can dealers order directly on WhatsApp?", 0, 1),
            ("demo_m4", ig_acc, asha_ig, Message.DIRECTION_INCOMING, "Saw your reel! Can you build an appointment booking bot for our clinic?", 1, 4),
            ("demo_m5", ig_acc, asha_ig, Message.DIRECTION_OUTGOING, "Hi Asha! Yes, we build Instagram + WhatsApp appointment bots.", 1, 3),
            ("demo_m6", wa_acc, asha_wa, Message.DIRECTION_INCOMING, "Connecting on WhatsApp as discussed on IG. What is the setup timeline and pricing?", 0, 2),
            ("demo_m7", ig_acc, asha_ig, Message.DIRECTION_INCOMING, "Can we get a custom quote for 2 clinic branches?", 0, 0),
            ("demo_m8", wa_acc, vikram_wa, Message.DIRECTION_INCOMING, "Hello, we saw your AI assistant demo for sales quotes.", 2, 5),
        ]
        created_msgs = {}
        for mid, acc, contact, direction, text, days_ago, hrs_ago in sample_msgs:
            msg, created = Message.objects.get_or_create(
                message_id=mid,
                defaults={
                    "platform_account": acc,
                    "contact": contact,
                    "direction": direction,
                    "message_type": Message.TYPE_TEXT,
                    "message_text": text,
                    "status": Message.STATUS_DELIVERED,
                },
            )
            if created:
                Message.objects.filter(pk=msg.pk).update(created_at=now - timedelta(days=days_ago, hours=hrs_ago))
            created_msgs[mid] = msg

        # Pending AI Replies (Approval Queue)
        if not PendingAIReply.objects.filter(status=PendingAIReply.STATUS_PENDING).exists():
            PendingAIReply.objects.create(
                platform_account=ig_acc,
                contact=asha_ig,
                trigger_message=created_msgs.get("demo_m7"),
                draft_text="Hi Asha! For 2 clinic branches, our appointment bot setup takes ~10 days and includes Instagram + WhatsApp sync. Would you like us to send a formal proposal?",
                reason="Pricing / custom multi-branch quote requires human approval",
            )
            PendingAIReply.objects.create(
                platform_account=wa_acc,
                contact=ravi_wa,
                trigger_message=created_msgs.get("demo_m3"),
                draft_text="Yes Ravi, dealers can browse your catalog and place orders directly inside WhatsApp, synced live with ERPNext. Shall we cover this in Thursday's demo?",
                reason="Account requires human approval before sending",
            )

        # Follow-up Scheduler items
        if not FollowUp.objects.exists():
            if ravi_cust:
                FollowUp.objects.create(
                    customer=ravi_cust,
                    opportunity=ravi_cust.opportunities.first(),
                    due_at=now - timedelta(hours=3),
                    channel=FollowUp.Channel.WHATSAPP,
                    note="Follow up on ERPNext 3-factory proposal",
                    status=FollowUp.Status.PENDING,
                )
            if asha_cust:
                FollowUp.objects.create(
                    customer=asha_cust,
                    opportunity=asha_cust.opportunities.first(),
                    due_at=now + timedelta(days=1),
                    channel=FollowUp.Channel.INSTAGRAM,
                    note="Send 2-branch clinic chatbot quote",
                    status=FollowUp.Status.PENDING,
                )
            if vikram_cust:
                FollowUp.objects.create(
                    customer=vikram_cust,
                    opportunity=vikram_cust.opportunities.first(),
                    due_at=now - timedelta(days=1),
                    channel=FollowUp.Channel.CALL,
                    note="Initial discovery call",
                    status=FollowUp.Status.DONE,
                    completed_at=now - timedelta(hours=20),
                )

        self.stdout.write(self.style.SUCCESS(
            f"Demo ready: {Customer.objects.count()} customers, {Opportunity.objects.count()} opportunities, "
            f"{Contact.objects.count()} contacts, {Message.objects.count()} messages, "
            f"{PendingAIReply.objects.filter(status='pending').count()} pending AI approvals, "
            f"{FollowUp.objects.count()} follow-ups."
        ))
