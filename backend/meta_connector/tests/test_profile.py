from unittest.mock import patch, MagicMock
from django.test import TestCase
from meta_connector.models import PlatformAccount, Contact
from meta_connector.services.profile_service import ProfileService


class ProfileServiceTests(TestCase):
    def setUp(self):
        self.ig_account = PlatformAccount.objects.create(
            platform=PlatformAccount.PLATFORM_INSTAGRAM,
            display_name="Instagram Business Page",
            instagram_id="17841400000000001",
            is_active=True
        )
        self.ig_account.set_access_token("fake_ig_token")
        self.ig_account.save()

        self.wa_account = PlatformAccount.objects.create(
            platform=PlatformAccount.PLATFORM_WHATSAPP,
            display_name="WhatsApp Business",
            phone_number_id="100998877665544",
            is_active=True
        )

    @patch('requests.get')
    def test_enrich_instagram_profile(self, mock_get):
        """Tests fetching name, username, and profile picture from Meta Graph API for Instagram."""
        contact = Contact.objects.create(
            platform_account=self.ig_account,
            platform_user_id="1234567890",
            username="1234567890",  # placeholder the webhook sets
        )

        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {
            "name": "Alex Rivera",
            "username": "alex_rivera_official",
            "profile_pic": "https://scontent.cdninstagram.com/v/t51.2885-15/avatar.jpg"
        }
        mock_get.return_value = mock_resp

        success = ProfileService.enrich_contact_profile(contact, self.ig_account)
        self.assertTrue(success)

        contact.refresh_from_db()
        self.assertEqual(contact.name, "Alex Rivera")
        self.assertEqual(contact.username, "alex_rivera_official")
        self.assertEqual(contact.profile_pic_url, "https://scontent.cdninstagram.com/v/t51.2885-15/avatar.jpg")
        self.assertIn("username", contact.raw_profile_data)

    def test_auto_extract_email_from_message(self):
        """Tests auto-detecting and storing contact email from message text."""
        contact = Contact.objects.create(
            platform_account=self.wa_account,
            platform_user_id="919876543210"
        )
        self.assertEqual(contact.email, "")

        text = "Hello! Please send my bill invoice to alex.dev@company.com ASAP."
        extracted = ProfileService.extract_contact_info_from_message(contact, text)
        self.assertTrue(extracted)

        contact.refresh_from_db()
        self.assertEqual(contact.email, "alex.dev@company.com")
