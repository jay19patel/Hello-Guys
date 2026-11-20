from unittest.mock import patch, MagicMock
from django.test import TestCase
from django.contrib.auth import get_user_model
from meta_connector.roles import AGENT, set_role
from rest_framework.test import APIClient
from rest_framework import status
from meta_connector.models import PlatformAccount, Contact, Message
from meta_connector.services.message_service import MessageService
from meta_connector.services.whatsapp_service import WhatsAppService
from meta_connector.services.instagram_service import InstagramService

User = get_user_model()


class MessageServiceAndAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()
        self.user = User.objects.create_user(username='admin_api', password='securepassword123')
        set_role(self.user, AGENT)  # sending needs Agent or above
        self.client.force_authenticate(user=self.user)

        self.wa_account = PlatformAccount.objects.create(
            platform=PlatformAccount.PLATFORM_WHATSAPP,
            display_name="Primary WhatsApp",
            phone_number_id="100998877665544",
            is_active=True
        )
        self.wa_account.set_access_token("fake_meta_access_token")
        self.wa_account.save()

        self.ig_account = PlatformAccount.objects.create(
            platform=PlatformAccount.PLATFORM_INSTAGRAM,
            display_name="Primary Instagram",
            instagram_id="17841400000000001",
            is_active=True
        )
        self.ig_account.set_access_token("fake_ig_access_token")
        self.ig_account.save()

    @patch('requests.post')
    def test_send_whatsapp_message_service(self, mock_post):
        """Tests WhatsAppService sends proper payload and headers."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "messaging_product": "whatsapp",
            "contacts": [{"input": "919876543210", "wa_id": "919876543210"}],
            "messages": [{"id": "wamid.HBgLMOTE5ODc2NTQzMjEQ..."}]
        }
        mock_post.return_value = mock_response

        success, data, error = WhatsAppService.send_whatsapp_message(
            to_number="919876543210",
            text="Your order is confirmed!",
            platform_account=self.wa_account
        )
        self.assertTrue(success)
        self.assertEqual(error, "")
        mock_post.assert_called_once()

    @patch('requests.post')
    def test_send_message_service_records_in_db(self, mock_post):
        """Verifies MessageService logs outgoing message into database."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "messages": [{"id": "wamid.TEST_OUTBOUND_123"}]
        }
        mock_post.return_value = mock_response

        msg, success, err = MessageService.send_message(
            platform='whatsapp',
            to='919876543210',
            text='Outbound test message',
            account_id=self.wa_account.id
        )

        self.assertTrue(success)
        self.assertIsNotNone(msg)
        self.assertEqual(msg.direction, Message.DIRECTION_OUTGOING)
        self.assertEqual(msg.status, Message.STATUS_SENT)
        self.assertEqual(msg.message_id, "wamid.TEST_OUTBOUND_123")

        # Contact must also have updated timestamp
        contact = Contact.objects.get(platform_user_id='919876543210')
        self.assertIsNotNone(contact.last_message_at)

    @patch.object(MessageService, 'send_message')
    def test_send_api_endpoint(self, mock_send_message):
        """Tests POST /api/meta/send/ endpoint via DRF."""
        fake_contact = Contact.objects.create(
            platform_account=self.wa_account,
            platform_user_id="919876543210"
        )
        fake_msg = Message.objects.create(
            platform_account=self.wa_account,
            contact=fake_contact,
            message_id="wamid.API_TEST_123",
            direction=Message.DIRECTION_OUTGOING,
            message_text="Hello from API",
            status=Message.STATUS_SENT
        )
        mock_send_message.return_value = (fake_msg, True, "")

        payload = {
            "platform": "whatsapp",
            "to": "919876543210",
            "text": "Hello from API"
        }
        response = self.client.post('/api/meta/send/', payload, format='json')
        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        self.assertEqual(response.data['message_id'], "wamid.API_TEST_123")
