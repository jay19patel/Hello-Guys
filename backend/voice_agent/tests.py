from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from crm.models import Activity, Customer, Opportunity
from voice_agent.models import VoiceBotSession, normalize_meeting_url


class VoiceAgentTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser("voiceadmin", password="pw")
        self.client.force_login(self.user)
        self.customer = Customer.objects.create(
            name="Asha Mehta",
            company="Mehta Clinic",
            email="asha@mehta.example",
            notes="Interested in 2-branch chatbot.",
        )
        self.opp = Opportunity.objects.create(
            customer=self.customer,
            title="Appointment chatbot",
            status=Opportunity.Status.QUALIFIED,
        )

    def test_normalize_meeting_url(self):
        self.assertEqual(normalize_meeting_url("abc-defg-hij"), "https://meet.google.com/abc-defg-hij")
        self.assertEqual(normalize_meeting_url("meet.google.com/xyz-uvwx-rst"), "https://meet.google.com/xyz-uvwx-rst")
        self.assertEqual(normalize_meeting_url("https://meet.google.com/abc-defg-hij"), "https://meet.google.com/abc-defg-hij")

    def test_voice_bot_start_status_stop_and_crm_activity_log(self):
        start_resp = self.client.post(
            reverse("voice_agent:start"),
            data={
                "lead_id": self.customer.pk,
                "opportunity_id": self.opp.pk,
                "meet_code_or_url": "abc-defg-hij",
                "bot_name": "Clinic AI",
                "custom_prompt": "Confirm demo slot.",
                "mode": "google_meet",
                "provider": "meetingbaas",
            },
            content_type="application/json",
        )
        self.assertEqual(start_resp.status_code, 201)
        payload = start_resp.json()
        self.assertTrue(payload["ok"])
        self.assertEqual(payload["meeting_url"], "https://meet.google.com/abc-defg-hij")
        session_id = payload["session_id"]

        session = VoiceBotSession.objects.get(pk=session_id)
        self.assertIn("Asha Mehta", session.system_prompt)
        self.assertIn("Mehta Clinic", session.system_prompt)
        self.assertIn("Confirm demo slot.", session.system_prompt)

        status_resp = self.client.get(reverse("voice_agent:status", args=[session_id]))
        self.assertEqual(status_resp.status_code, 200)
        self.assertEqual(status_resp.json()["bot_name"], "Clinic AI")

        stop_resp = self.client.post(
            reverse("voice_agent:stop"),
            data={"session_id": session_id},
            content_type="application/json",
        )
        self.assertEqual(stop_resp.status_code, 200)
        self.assertEqual(stop_resp.json()["status"], VoiceBotSession.Status.COMPLETED)

        self.assertTrue(
            Activity.objects.filter(customer=self.customer, opportunity=self.opp, kind=Activity.Kind.MEETING).exists()
        )
        session.refresh_from_db()
        self.assertIn("asha@mehta.example", session.mom_emailed_to)

    @patch("voice_agent.services.subprocess.Popen")
    def test_self_hosted_playwright_provider_launch_and_stop(self, mock_popen):
        mock_popen.return_value = MagicMock(pid=43210)
        start_resp = self.client.post(
            reverse("voice_agent:start"),
            data={
                "lead_id": self.customer.pk,
                "meet_code_or_url": "xyz-uvwx-rst",
                "bot_name": "Self Hosted Bot",
                "mode": "google_meet",
                "provider": "self_hosted",
            },
            content_type="application/json",
        )
        self.assertEqual(start_resp.status_code, 201)
        data = start_resp.json()
        self.assertEqual(data["external_bot_id"], "pid:43210")
        self.assertEqual(data["status"], VoiceBotSession.Status.QUEUED)
        mock_popen.assert_called_once()

        with patch("voice_agent.services.os.kill") as mock_kill:
            stop_resp = self.client.post(
                reverse("voice_agent:stop"),
                data={"session_id": data["session_id"]},
                content_type="application/json",
            )
            self.assertEqual(stop_resp.status_code, 200)
            self.assertEqual(stop_resp.json()["status"], VoiceBotSession.Status.COMPLETED)
            mock_kill.assert_called_once()
