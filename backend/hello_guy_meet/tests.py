import json

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from hello_guy_meet.models import MeetMinutes, MeetRoom, MeetSettings


class HelloGuyMeetTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser("meetadmin", password="pw")
        self.client.force_login(self.user)

    def test_create_room_guest_access_and_end_mom_table(self):
        resp = self.client.post(
            reverse("meet:dashboard"),
            data={
                "title": "Product Discovery Call",
                "ai_enabled": True,
                "bot_name": "Meet AI",
                "system_prompt": "Focus on product demo.",
            },
        )
        self.assertEqual(resp.status_code, 302)
        room = MeetRoom.objects.get(title="Product Discovery Call")
        self.assertEqual(room.system_prompt, "Focus on product demo.")

        # Guest (logged out) can open the WebRTC room URL with no email/login, and cannot see host AI toggle
        self.client.logout()
        guest_resp = self.client.get(reverse("meet:room", args=[room.room_code]))
        self.assertEqual(guest_resp.status_code, 200)
        self.assertContains(guest_resp, room.room_code)
        self.assertContains(guest_resp, 'id="meet-config"')
        self.assertContains(guest_resp, 'guestNameInput')
        self.assertNotContains(guest_resp, '@click="toggleAI()"')

        # Host can toggle AI from Meeting Rooms dashboard
        self.client.force_login(self.user)
        toggle_resp = self.client.post(reverse("meet:room-toggle-ai", args=[room.room_code]))
        self.assertEqual(toggle_resp.status_code, 302)
        room.refresh_from_db()
        self.assertFalse(room.ai_enabled)

        # Host ends meeting with transcript -> saves MoM to MeetMinutes table
        self.client.force_login(self.user)
        end_resp = self.client.post(
            reverse("meet:room-end", args=[room.room_code]),
            data=json.dumps({
                "transcript": [
                    {"role": "user", "speaker": "Asha Mehta", "text": "We need WhatsApp + Meet integration."},
                    {"role": "assistant", "speaker": "Meet AI", "text": "Noted! I will include both in the proposal."},
                ]
            }),
            content_type="application/json",
        )
        self.assertEqual(end_resp.status_code, 200)
        data = end_resp.json()
        self.assertTrue(data["ok"])
        self.assertEqual(data["status"], MeetRoom.Status.ENDED)
        self.assertIn("WhatsApp", data["call_summary"])

        minutes = MeetMinutes.objects.get(room=room)
        self.assertEqual(minutes.title, "Product Discovery Call")
        self.assertIn("Asha Mehta", minutes.participants)
        self.assertEqual(len(minutes.transcript), 2)

        mom_resp = self.client.get(reverse("meet:mom-list"))
        self.assertEqual(mom_resp.status_code, 200)
        self.assertContains(mom_resp, "Product Discovery Call")

    def test_meet_settings_page(self):
        resp = self.client.get(reverse("meet:settings"))
        self.assertEqual(resp.status_code, 200)
        post_resp = self.client.post(
            reverse("meet:settings"),
            data={
                "default_bot_name": "NJTech AI",
                "ws_base_url": "wss://meet.example.com",
                "llm_model": "gpt-4o-mini",
                "stt_provider": MeetSettings.STTProvider.DEEPGRAM,
                "tts_provider": MeetSettings.TTSProvider.CARTESIA,
                "cartesia_voice_id": "voice-123",
                "ai_auto_join_default": True,
                "allow_interruptions": True,
                "default_system_prompt": "Be concise.",
                "ice_servers_json": '[{"urls": "stun:stun.l.google.com:19302"}]',
            },
        )
        self.assertEqual(post_resp.status_code, 302)
        cfg = MeetSettings.get_solo()
        self.assertEqual(cfg.default_bot_name, "NJTech AI")
        self.assertEqual(cfg.ws_base_url, "wss://meet.example.com")

    def test_meet_calendar_view_and_schedule(self):
        get_resp = self.client.get(reverse("meet:calendar"))
        self.assertEqual(get_resp.status_code, 200)
        post_resp = self.client.post(
            reverse("meet:calendar"),
            data={
                "title": "Scheduled Demo",
                "scheduled_start": "2026-11-26T10:00",
                "scheduled_end": "2026-11-26T11:00",
                "ai_enabled": True,
                "bot_name": "Demo Bot",
            },
        )
        self.assertEqual(post_resp.status_code, 302)
        self.assertTrue(MeetRoom.objects.filter(title="Scheduled Demo").exists())
