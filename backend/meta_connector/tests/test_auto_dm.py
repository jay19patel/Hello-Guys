from unittest.mock import patch
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from meta_connector.models import AutoDMLog, AutoDMRule, Contact, Message, PlatformAccount
from meta_connector.services.webhook_processor import process_webhook_payload
from meta_connector.models import WebhookLog

IG_ID = "17841400000000001"
SEND = 'meta_connector.services.instagram_service.InstagramService.send_private_reply'
REPLY = 'meta_connector.services.instagram_service.InstagramService.reply_to_comment'


def comment_payload(comment_id, text, sender_id="555", username="fan_1", media_id="MEDIA_1"):
    return {"object": "instagram", "entry": [{"id": IG_ID, "time": 1, "changes": [{
        "field": "comments",
        "value": {"id": comment_id, "text": text, "from": {"id": sender_id, "username": username},
                  "media": {"id": media_id, "media_product_type": "REELS"}},
    }]}]}


class AutoDMTests(TestCase):
    def setUp(self):
        self.account = PlatformAccount.objects.create(
            platform='instagram', display_name='IG', instagram_id=IG_ID, is_active=True)

    def receive(self, payload):
        process_webhook_payload(WebhookLog.objects.create(platform='instagram', payload=payload).id)

    @patch(REPLY, return_value=(True, {}, ''))
    @patch(SEND, return_value=(True, {'message_id': 'mid.dm1'}, ''))
    def test_post_rule_sends_dm_once_per_user_and_public_reply(self, send, reply):
        AutoDMRule.objects.create(platform_account=self.account, media_id='MEDIA_1',
                                  message='Here is the link', public_reply='Check DM')
        self.receive(comment_payload('c1', 'nice'))
        send.assert_called_once_with(self.account, 'c1', 'Here is the link')
        reply.assert_called_once_with(self.account, 'c1', 'Check DM')

        contact = Contact.objects.get(platform_user_id='555')
        self.assertEqual(contact.username, 'fan_1')
        self.assertTrue(Message.objects.filter(message_id='c1', message_type='comment').exists())
        self.assertTrue(Message.objects.filter(message_id='mid.dm1', direction='outgoing').exists())

        # same user comments again on same post -> skipped; Meta retry of c1 -> ignored entirely
        self.receive(comment_payload('c2', 'again'))
        self.receive(comment_payload('c1', 'nice'))
        self.assertEqual(send.call_count, 1)
        self.assertEqual(list(AutoDMLog.objects.order_by('id').values_list('status', flat=True)), ['sent', 'skipped'])

    @patch(SEND, return_value=(True, {}, ''))
    def test_default_rule_keywords_and_post_override(self, send):
        AutoDMRule.objects.create(platform_account=self.account, media_id='', message='Default', keywords='price, link')
        AutoDMRule.objects.create(platform_account=self.account, media_id='MEDIA_OFF', message='x', is_enabled=False)

        self.receive(comment_payload('c1', 'wow', sender_id='1'))                         # no keyword
        self.receive(comment_payload('c2', 'What PRICE?', sender_id='2'))                  # keyword hit
        self.receive(comment_payload('c3', 'price', sender_id='3', media_id='MEDIA_OFF'))  # post rule off wins
        send.assert_called_once_with(self.account, 'c2', 'Default')
        self.assertEqual(AutoDMLog.objects.filter(status='skipped').count(), 2)

    @patch(SEND)
    def test_own_comments_and_no_rule_do_nothing(self, send):
        self.receive(comment_payload('c1', 'hi', sender_id='9'))  # no rule configured
        AutoDMRule.objects.create(platform_account=self.account, message='m')
        self.receive(comment_payload('c2', 'Check DM', sender_id=IG_ID))  # our own public reply
        send.assert_not_called()
        self.assertFalse(AutoDMLog.objects.exists())
        self.assertFalse(Message.objects.filter(message_id='c2').exists())

    @patch(SEND, return_value=(False, {}, 'Permission missing'))
    def test_failed_send_is_logged(self, send):
        AutoDMRule.objects.create(platform_account=self.account, message='m')
        self.receive(comment_payload('c1', 'hi'))
        log = AutoDMLog.objects.get()
        self.assertEqual((log.status, log.detail), ('failed', 'Permission missing'))


class AccountPageTests(TestCase):
    def setUp(self):
        self.account = PlatformAccount.objects.create(platform='instagram', display_name='IG', instagram_id=IG_ID)
        self.url = reverse('dashboard-account', args=[self.account.pk])
        self.client.force_login(User.objects.create_superuser('a', password='p'))

    @patch('meta_connector.services.instagram_service.InstagramService.list_media',
           return_value=(True, {'data': [{'id': 'M1', 'caption': 'My reel', 'media_type': 'VIDEO',
                                          'media_product_type': 'REELS', 'permalink': 'https://instagram.com/reel/x'}],
                                'paging': {'cursors': {'after': 'CUR'}, 'next': 'https://next'}}, ''))
    @patch('meta_connector.services.instagram_service.InstagramService.get_profile',
           return_value=(True, {'username': 'njtech', 'followers_count': 28}, ''))
    def test_page_lists_media_and_saves_rule(self, profile, media):
        resp = self.client.get(self.url)
        self.assertContains(resp, '@njtech')
        self.assertContains(resp, 'My reel')
        self.assertContains(resp, '?after=CUR')

        resp = self.client.post(self.url, {'media_id': 'M1', 'mM1-is_enabled': 'on', 'mM1-message': 'Hello!'})
        self.assertRedirects(resp, self.url + '#media-M1', fetch_redirect_response=False)
        rule = AutoDMRule.objects.get(media_id='M1')
        self.assertEqual((rule.message, rule.is_enabled), ('Hello!', True))
        self.assertContains(self.client.get(self.url), 'Auto-DM ON')

        self.client.post(self.url, {'media_id': 'M1', 'action': 'remove'})
        self.assertFalse(AutoDMRule.objects.exists())

    @patch('meta_connector.services.instagram_service.InstagramService.list_media', return_value=(False, {}, 'Token expired'))
    @patch('meta_connector.services.instagram_service.InstagramService.get_profile', return_value=(False, {}, 'Token expired'))
    def test_meta_errors_are_shown(self, *_):
        self.assertContains(self.client.get(self.url), 'Token expired')

    def test_whatsapp_account_redirects(self):
        wa = PlatformAccount.objects.create(platform='whatsapp', display_name='WA', phone_number_id='1')
        self.assertRedirects(self.client.get(reverse('dashboard-account', args=[wa.pk])), reverse('dashboard-settings'))
