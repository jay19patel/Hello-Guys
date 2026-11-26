from decimal import Decimal

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from hello_guy_ai.models import Document, KnowledgeBase, Run, Thread


class AIAnalyticsTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user("aistaff", password="pw", is_staff=True)
        self.regular = User.objects.create_user("regular", password="pw", is_staff=False)

    def test_analytics_requires_staff_and_renders_metrics(self):
        url = reverse("hello_guy_ai_analytics")
        # Non-staff user gets 403
        self.client.force_login(self.regular)
        self.assertEqual(self.client.get(url).status_code, 403)

        # Create sample AI data
        thread = Thread.objects.create(created_by=self.staff, assistant_id="website_bot", name="Test Thread")
        Run.objects.create(
            thread=thread,
            status=Run.Status.SUCCEEDED,
            model="gpt-4o-mini",
            prompt_tokens=120,
            completion_tokens=80,
            cost_usd=Decimal("0.001200"),
            feedback_rating="up",
        )
        Run.objects.create(
            thread=thread,
            status=Run.Status.FAILED,
            model="gpt-4o-mini",
            error="RateLimitError: 429",
        )
        kb = KnowledgeBase.objects.create(slug="faq", name="FAQ")
        Document.objects.create(knowledge_base=kb, title="Pricing", status=Document.Status.READY, chunk_count=12)

        self.client.force_login(self.staff)
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200)
        self.assertContains(resp, "Hello Guy AI Analytics")
        self.assertContains(resp, "50.0% success")
        self.assertContains(resp, "gpt-4o-mini")
        self.assertContains(resp, "RateLimitError: 429")

    def test_poll_returns_only_live_runs_and_other_users_reminders_have_no_buttons(self):
        from django.utils import timezone

        from hello_guy_ai.models import Reminder

        for status in (Run.Status.RUNNING, Run.Status.PAUSED):  # one active run per thread
            Run.objects.create(thread=Thread.objects.create(created_by=self.staff, assistant_id="website_bot"), status=status)
        self.client.force_login(self.staff)
        data = self.client.get(reverse("hello_guy_ai_analytics"), {"format": "json"}).json()
        self.assertEqual(
            (set(data), data["active_runs"], data["paused_runs"], len(data["live_runs"])),
            ({"live_runs", "active_runs", "paused_runs"}, 1, 1, 2),
        )

        mine = Reminder.objects.create(user=self.staff, message="my task", due_at=timezone.now())
        theirs = Reminder.objects.create(user=self.regular, message="their task", due_at=timezone.now())
        html = self.client.get(reverse("hello_guy_ai_analytics")).content.decode()
        self.assertIn(f"aiReminderAction({mine.pk},", html)
        self.assertNotIn(f"aiReminderAction({theirs.pk},", html)
        self.assertIn("Owner only", html)
