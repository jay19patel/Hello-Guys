"""Seed realistic demo Projects, Tasks, and Subtasks for PMS. Safe to run again (idempotent)."""

from datetime import timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand
from django.utils import timezone

from pms.models import Project, Task


class Command(BaseCommand):
    help = "Create demo PMS projects, tasks, and subtasks (idempotent)"

    def handle(self, *args, **options):
        User = get_user_model()
        users = list(User.objects.filter(is_active=True).order_by("-is_superuser", "id"))
        if not users:
            self.stdout.write(self.style.WARNING("No active users found. Create a user first."))
            return

        owner = users[0]
        today = timezone.localdate()

        def pick_user(idx: int):
            return users[idx % len(users)]

        def ensure_task(project: Project, title: str, *, parent=None, is_group=False, status=Task.Status.OPEN,
                        priority=Task.Priority.MEDIUM, assignee=None, due_offset=None, exp="0", act="0"):
            due = (today + timedelta(days=due_offset)) if due_offset is not None else None
            task, _ = Task.objects.get_or_create(
                project=project,
                parent=parent,
                title=title,
                defaults={
                    "is_group": is_group,
                    "status": status,
                    "priority": priority,
                    "assignee": assignee,
                    "due_date": due,
                    "expected_hours": Decimal(exp),
                    "actual_hours": Decimal(act),
                },
            )
            return task

        # 1. Website Redesign (3-level hierarchy)
        p1, p1_created = Project.objects.get_or_create(
            name="Hello Guys Website Redesign",
            defaults={
                "description": "Full redesign of marketing site, interactive product tour, and mobile responsiveness.",
                "status": Project.Status.ACTIVE,
                "owner": owner,
                "start_date": today - timedelta(days=10),
                "due_date": today + timedelta(days=20),
            },
        )
        p1.members.add(*users[:4])
        if p1_created or not p1.tasks.exists():
            design = ensure_task(p1, "UI/UX Design", is_group=True, status=Task.Status.WORKING, priority=Task.Priority.HIGH, assignee=owner, due_offset=5, exp="2")
            wire = ensure_task(p1, "Wireframes & Flows", parent=design, is_group=True, status=Task.Status.WORKING, assignee=pick_user(1), due_offset=2, exp="4", act="3.5")
            ensure_task(p1, "Desktop landing wireframe", parent=wire, status=Task.Status.DONE, assignee=pick_user(1), due_offset=-2, exp="6", act="5.5")
            ensure_task(p1, "Mobile responsive layouts", parent=wire, status=Task.Status.DONE, assignee=pick_user(2), due_offset=-1, exp="4", act="4")
            ensure_task(p1, "Design system & tokens", parent=design, status=Task.Status.REVIEW, priority=Task.Priority.HIGH, assignee=owner, due_offset=3, exp="5", act="4.5")

            dev = ensure_task(p1, "Frontend Development", is_group=True, status=Task.Status.WORKING, priority=Task.Priority.URGENT, assignee=owner, due_offset=12)
            ensure_task(p1, "Build hero & pricing sections", parent=dev, status=Task.Status.WORKING, priority=Task.Priority.HIGH, assignee=pick_user(1), due_offset=4, exp="8", act="3")
            ensure_task(p1, "Integrate contact inquiry form", parent=dev, status=Task.Status.OPEN, assignee=pick_user(2), due_offset=7, exp="4")
            ensure_task(p1, "Lighthouse performance & SEO pass", parent=dev, status=Task.Status.OPEN, priority=Task.Priority.LOW, assignee=owner, due_offset=11, exp="3")

            ensure_task(p1, "Production deployment & DNS check", status=Task.Status.OPEN, priority=Task.Priority.HIGH, assignee=owner, due_offset=18, exp="2")

        # 2. CRM & WhatsApp Automation
        p2, p2_created = Project.objects.get_or_create(
            name="Enterprise CRM & WhatsApp Automation",
            defaults={
                "description": "End-to-end WhatsApp Cloud API, AI auto-reply, Google Calendar sync, and lead pipeline.",
                "status": Project.Status.ACTIVE,
                "owner": owner,
                "start_date": today - timedelta(days=5),
                "due_date": today + timedelta(days=15),
            },
        )
        p2.members.add(*users[:4])
        if p2_created or not p2.tasks.exists():
            setup = ensure_task(p2, "Meta & Webhook Setup", is_group=True, status=Task.Status.DONE, assignee=owner, due_offset=-1)
            ensure_task(p2, "Verify WhatsApp Business number", parent=setup, status=Task.Status.DONE, assignee=owner, due_offset=-3, exp="2", act="2")
            ensure_task(p2, "Configure webhook signature check", parent=setup, status=Task.Status.DONE, assignee=pick_user(1), due_offset=-2, exp="3", act="2.5")

            ai_grp = ensure_task(p2, "AI Sales Assistant & Calendar", is_group=True, status=Task.Status.WORKING, priority=Task.Priority.HIGH, assignee=owner, due_offset=6)
            ensure_task(p2, "Connect Google Calendar OAuth", parent=ai_grp, status=Task.Status.DONE, assignee=owner, due_offset=-1, exp="3", act="3")
            ensure_task(p2, "Test lead qualification prompts", parent=ai_grp, status=Task.Status.WORKING, priority=Task.Priority.URGENT, assignee=pick_user(1), due_offset=-1, exp="5", act="2")
            ensure_task(p2, "Client UAT walkthrough", parent=ai_grp, status=Task.Status.OPEN, assignee=owner, due_offset=5, exp="2")

        # 3. Q4 Marketing Campaign (On Hold)
        p3, p3_created = Project.objects.get_or_create(
            name="Q4 Product Launch Campaign",
            defaults={
                "description": "Launch video, case studies, and social media rollout.",
                "status": Project.Status.ON_HOLD,
                "owner": owner,
                "start_date": today,
                "due_date": today + timedelta(days=30),
            },
        )
        p3.members.add(*users[:3])
        if p3_created or not p3.tasks.exists():
            vid = ensure_task(p3, "Launch Video Production", is_group=True, status=Task.Status.OPEN, priority=Task.Priority.MEDIUM, assignee=owner, due_offset=14)
            ensure_task(p3, "Write 60s script", parent=vid, status=Task.Status.DONE, assignee=pick_user(1), due_offset=2, exp="3", act="3")
            ensure_task(p3, "Record product demo clips", parent=vid, status=Task.Status.OPEN, assignee=owner, due_offset=8, exp="6")
            ensure_task(p3, "Final edit & captions", parent=vid, status=Task.Status.OPEN, assignee=pick_user(2), due_offset=13, exp="5")

        self.stdout.write(
            self.style.SUCCESS(
                f"Seeded PMS demo data: {Project.objects.count()} projects, {Task.objects.count()} tasks."
            )
        )
