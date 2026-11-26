from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.exceptions import ImproperlyConfigured
from django.test import TestCase, override_settings
from django.urls import reverse

from meta_connector.models import Contact, PendingAIReply, PlatformAccount
from meta_connector.roles import AGENT, VIEWER, set_role
from meta_connector.services import SendError
from meta_connector.utils import get_fernet_instance, verify_meta_signature

SEND = 'meta_connector.views.approval_views.send_message'


class ApprovalTests(TestCase):
    def setUp(self):
        self.account = PlatformAccount.objects.create(platform='instagram', display_name='IG', instagram_id='IG1')
        self.contact = Contact.objects.create(platform_account=self.account, platform_user_id='555', name='Riya')
        self.item = PendingAIReply.objects.create(platform_account=self.account, contact=self.contact, draft_text='Hello!')
        self.url = reverse('dashboard-approval-action', args=[self.item.pk])
        self.agent = User.objects.create_user('agent', password='pw-12345678')
        set_role(self.agent, AGENT)

    def test_viewer_cannot_open_queue_or_act(self):
        viewer = User.objects.create_user('viewer', password='pw-12345678')
        set_role(viewer, VIEWER)
        self.client.force_login(viewer)
        self.assertEqual(self.client.get(reverse('dashboard-approvals')).status_code, 403)
        self.assertEqual(self.client.post(self.url, {'action': 'approve'}).status_code, 403)
        self.client.force_login(self.agent)
        self.assertEqual(self.client.get(reverse('dashboard-approvals') + '?status=failed').status_code, 200)

    def test_double_approve_sends_once(self):
        self.client.force_login(self.agent)
        with patch(SEND, return_value=None) as send:
            self.client.post(self.url, {'action': 'approve', 'reply_text': 'Hello!'})
            self.client.post(self.url, {'action': 'approve', 'reply_text': 'Hello!'})
        self.assertEqual(send.call_count, 1)
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, PendingAIReply.STATUS_APPROVED)

    def test_claimed_row_is_not_sent_again(self):
        PendingAIReply.objects.filter(pk=self.item.pk).update(status=PendingAIReply.STATUS_SENDING)
        self.client.force_login(self.agent)
        with patch(SEND, return_value=None) as send:
            self.client.post(self.url, {'action': 'approve', 'reply_text': 'Hello!'})
        send.assert_not_called()

    def test_failed_send_can_be_retried(self):
        self.client.force_login(self.agent)
        with patch(SEND, side_effect=SendError('boom')):
            self.client.post(self.url, {'action': 'approve', 'reply_text': 'Hello!'})
        self.item.refresh_from_db()
        self.assertEqual((self.item.status, self.item.error), (PendingAIReply.STATUS_FAILED, 'boom'))
        with patch(SEND, return_value=None) as send:
            self.client.post(self.url, {'action': 'approve', 'reply_text': 'Hello again'})
        self.assertEqual(send.call_count, 1)
        self.item.refresh_from_db()
        self.assertEqual((self.item.status, self.item.edited_text), (PendingAIReply.STATUS_APPROVED, 'Hello again'))

    def test_edited_reply_over_platform_limit_is_refused(self):
        self.client.force_login(self.agent)
        with patch(SEND, return_value=None) as send:
            self.client.post(self.url, {'action': 'approve', 'reply_text': 'x' * 1001})
        send.assert_not_called()
        self.item.refresh_from_db()
        self.assertEqual(self.item.status, PendingAIReply.STATUS_PENDING)

    def test_next_must_stay_on_site(self):
        self.client.force_login(self.agent)
        resp = self.client.post(self.url, {'action': 'reject', 'next': 'https://evil.example/'})
        self.assertRedirects(resp, reverse('dashboard-approvals'), fetch_redirect_response=False)


class UtilsTests(TestCase):
    def test_signature_requires_prefix(self):
        import hashlib, hmac
        digest = hmac.new(b's', b'body', hashlib.sha256).hexdigest()
        self.assertTrue(verify_meta_signature(b'body', f'sha256={digest}', 's'))
        self.assertFalse(verify_meta_signature(b'body', digest, 's'))

    def test_malformed_encryption_key_rejected_outside_debug(self):
        with override_settings(FIELD_ENCRYPTION_KEY='not-a-fernet-key', DEBUG=False):
            with self.assertRaises(ImproperlyConfigured):
                get_fernet_instance()
        with override_settings(FIELD_ENCRYPTION_KEY='not-a-fernet-key', DEBUG=True):
            get_fernet_instance()
