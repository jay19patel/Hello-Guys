import hashlib
import hmac
import json
from django.test import TestCase, override_settings
from django.urls import reverse
from meta_connector.models import MetaConfig, PlatformAccount, Contact, Message


@override_settings(META_WEBHOOK_ASYNC=False)  # process inline so assertions see the result
class MetaWebhookTests(TestCase):
    def setUp(self):
        self.verify_token = "test_meta_verify_token_123"
        self.app_secret = "test_meta_app_secret_456"
        self.webhook_url = reverse('meta_connector:meta-webhook')

        # Create sample platform accounts
        self.wa_account = PlatformAccount.objects.create(
            platform=PlatformAccount.PLATFORM_WHATSAPP,
            display_name="Test WhatsApp",
            phone_number_id="100998877665544",
            verify_token=self.verify_token,
            is_active=True
        )
        self.ig_account = PlatformAccount.objects.create(
            platform=PlatformAccount.PLATFORM_INSTAGRAM,
            display_name="Test Instagram",
            instagram_id="17841400000000001",
            verify_token=self.verify_token,
            is_active=True
        )

    # --- 1. Webhook Verification Tests (GET) ---

    def test_get_webhook_verification_success(self):
        """Valid subscription challenge returns 200 and mirrors hub.challenge."""
        params = {
            'hub.mode': 'subscribe',
            'hub.verify_token': self.verify_token,
            'hub.challenge': 'CHALLENGE_STRING_XYZ_789'
        }
        response = self.client.get(self.webhook_url, params)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode('utf-8'), 'CHALLENGE_STRING_XYZ_789')

    def test_get_webhook_verification_invalid_token(self):
        """Invalid verify token returns 403 Forbidden."""
        params = {
            'hub.mode': 'subscribe',
            'hub.verify_token': 'wrong_token',
            'hub.challenge': 'CHALLENGE_STRING_XYZ_789'
        }
        response = self.client.get(self.webhook_url, params)
        self.assertEqual(response.status_code, 403)

    # --- 2. Webhook Signature Verification Tests (POST) ---

    def test_post_webhook_with_valid_signature(self):
        """Requests with valid X-Hub-Signature-256 header are accepted."""
        MetaConfig.objects.create(app_secret=self.app_secret)
        payload_dict = {
            "object": "whatsapp_business_account",
            "entry": []
        }
        raw_body = json.dumps(payload_dict).encode('utf-8')
        signature_hash = hmac.new(self.app_secret.encode('utf-8'), raw_body, hashlib.sha256).hexdigest()

        response = self.client.post(
            self.webhook_url,
            data=raw_body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256=f"sha256={signature_hash}"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content.decode('utf-8'), "EVENT_RECEIVED")

    def test_post_webhook_with_invalid_signature(self):
        """Requests with mismatched signature are rejected with 403 Forbidden."""
        MetaConfig.objects.create(app_secret=self.app_secret)
        raw_body = b'{"object": "whatsapp_business_account"}'
        response = self.client.post(
            self.webhook_url,
            data=raw_body,
            content_type="application/json",
            HTTP_X_HUB_SIGNATURE_256="sha256=bad_tampered_hash_12345"
        )
        self.assertEqual(response.status_code, 403)

    # --- 3. WhatsApp Payload Processing & Deduplication Tests ---

    def test_whatsapp_message_processing_and_deduplication(self):
        """Verifies parsing of incoming WhatsApp text messages, contact creation, and deduplication."""
        message_id = "wamid.HBgLMOTE5ODc2NTQzMjEQ..."
        sender_phone = "919876543210"

        wa_payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WHATSAPP_BUSINESS_ACCOUNT_ID",
                    "changes": [
                        {
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": "15550234567",
                                    "phone_number_id": "100998877665544"
                                },
                                "contacts": [
                                    {
                                        "profile": {"name": "Alice Johnson"},
                                        "wa_id": sender_phone
                                    }
                                ],
                                "messages": [
                                    {
                                        "from": sender_phone,
                                        "id": message_id,
                                        "timestamp": "1732089600",
                                        "text": {"body": "Hello, I want to inquire about my order."},
                                        "type": "text"
                                    }
                                ]
                            },
                            "field": "messages"
                        }
                    ]
                }
            ]
        }

        # First delivery: creates WebhookLog, Contact, and Message
        response = self.client.post(
            self.webhook_url,
            data=json.dumps(wa_payload),
            content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)

        # Ensure Contact was created
        contact = Contact.objects.filter(platform_user_id=sender_phone).first()
        self.assertIsNotNone(contact)
        self.assertEqual(contact.name, "Alice Johnson")
        self.assertEqual(contact.phone, sender_phone)
        self.assertIsNotNone(contact.last_message_at)

        # Ensure Message was created
        msg_count = Message.objects.filter(message_id=message_id).count()
        self.assertEqual(msg_count, 1)

        msg = Message.objects.get(message_id=message_id)
        self.assertEqual(msg.direction, Message.DIRECTION_INCOMING)
        self.assertEqual(msg.message_type, Message.TYPE_TEXT)
        self.assertEqual(msg.message_text, "Hello, I want to inquire about my order.")
        self.assertEqual(msg.contact, contact)

        # Deduplication check: Simulate Meta webhook resending the exact same message payload
        response_dup = self.client.post(
            self.webhook_url,
            data=json.dumps(wa_payload),
            content_type="application/json"
        )
        self.assertEqual(response_dup.status_code, 200)

        # Message count must STILL be exactly 1!
        self.assertEqual(Message.objects.filter(message_id=message_id).count(), 1)

    # --- 4. Instagram Payload Processing & Deduplication Tests ---

    def test_instagram_message_processing_and_deduplication(self):
        """Verifies parsing of incoming Instagram messages and deduplication."""
        mid = "mid.1456000000000:abc123"
        ig_user_id = "123456789012345"

        ig_payload = {
            "object": "instagram",
            "entry": [
                {
                    "id": "17841400000000001",
                    "time": 1732089600,
                    "messaging": [
                        {
                            "sender": {"id": ig_user_id},
                            "recipient": {"id": "17841400000000001"},
                            "timestamp": 1732089600,
                            "message": {
                                "mid": mid,
                                "text": "Hi from Instagram DM!"
                            }
                        }
                    ]
                }
            ]
        }

        response = self.client.post(
            self.webhook_url,
            data=json.dumps(ig_payload),
            content_type="application/json"
        )
        self.assertEqual(response.status_code, 200)

        contact = Contact.objects.filter(platform_user_id=ig_user_id).first()
        self.assertIsNotNone(contact)

        msg = Message.objects.filter(message_id=mid).first()
        self.assertIsNotNone(msg)
        self.assertEqual(msg.message_text, "Hi from Instagram DM!")
        self.assertEqual(msg.direction, Message.DIRECTION_INCOMING)

        # Second delivery: verify deduplication
        response2 = self.client.post(
            self.webhook_url,
            data=json.dumps(ig_payload),
            content_type="application/json"
        )
        self.assertEqual(response2.status_code, 200)
        self.assertEqual(Message.objects.filter(message_id=mid).count(), 1)
