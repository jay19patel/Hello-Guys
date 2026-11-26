import hashlib
import hmac
import json
from datetime import timedelta
from unittest.mock import patch
from django.contrib.auth.models import Group, User
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from meta_connector.models import AutoDMRule, Contact, Message, MetaConfig, PlatformAccount, WebhookLog
from meta_connector.roles import ADMIN, AGENT, VIEWER, get_role, set_role
from meta_connector.services.maintenance import cleanup_webhook_logs, problem_webhooks, retry_webhooks
from meta_connector.services.message_service import MessageService

SEND = 'meta_connector.services.message_service.MessageService.send_message'


def make_user(name, role):
    user = User.objects.create_user(name, password='pw-12345678')
    set_role(user, role)
    return user


class RoleTests(TestCase):
    def setUp(self):
        self.account = PlatformAccount.objects.create(platform='instagram', display_name='IG', instagram_id='IG1')
        self.contact = Contact.objects.create(platform_account=self.account, platform_user_id='555', name='Riya')
        self.contact_url = reverse('dashboard-contact', args=[self.contact.pk])
        self.account_url = reverse('dashboard-account', args=[self.account.pk])

    def login(self, role):
        user = make_user(role.lower(), role)
        self.client.force_login(user)
        return user

    def test_set_role_groups_and_admin_access(self):
        u = make_user('a', AGENT)
        self.assertEqual((get_role(u), u.is_staff), (AGENT, False))
        set_role(u, ADMIN)
        u = User.objects.get(pk=u.pk)
        self.assertEqual((get_role(u), u.is_staff, u.is_superuser), (ADMIN, True, True))
        self.assertEqual(list(u.groups.values_list('name', flat=True)), [ADMIN])

    def test_viewer_reads_only(self):
        self.login(VIEWER)
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 200)
        page = self.client.get(self.contact_url)
        self.assertContains(page, 'View only')
        self.assertNotContains(page, 'id="replyText"')
        with patch(SEND) as send:
            self.assertEqual(self.client.post(self.contact_url, {'text': 'hi'}).status_code, 403)
        send.assert_not_called()
        self.assertEqual(self.client.get(reverse('dashboard-settings')).status_code, 403)
        self.assertEqual(self.client.post(self.account_url, {'media_id': 'M', 'mM-message': 'x'}).status_code, 403)
        self.assertNotContains(self.client.get(reverse('dashboard')), 'Settings</a>')

    def test_agent_replies_and_sets_auto_dm_but_cannot_delete_or_open_settings(self):
        agent = self.login(AGENT)
        with patch(SEND, return_value=(None, True, '')) as send:
            self.client.post(self.contact_url, {'text': 'hello'})
        self.assertEqual(send.call_args.kwargs['sent_by'], agent)
        self.client.post(self.account_url, {'media_id': 'M', 'mM-is_enabled': 'on', 'mM-message': 'Link here'})
        self.assertTrue(AutoDMRule.objects.filter(media_id='M').exists())
        self.assertEqual(self.client.post(self.account_url, {'media_id': 'M', 'action': 'remove'}).status_code, 403)
        self.assertTrue(AutoDMRule.objects.filter(media_id='M').exists())
        self.assertEqual(self.client.get(reverse('dashboard-settings')).status_code, 403)

    def test_api_roles(self):
        self.login(VIEWER)
        self.assertEqual(self.client.get('/api/meta/contacts/').status_code, 200)
        self.assertEqual(self.client.post('/api/meta/send/', {'platform': 'instagram', 'to': '1', 'text': 'x'}).status_code, 403)

    def test_no_role_and_anonymous(self):
        self.assertRedirects(self.client.get(reverse('dashboard')), reverse('dashboard-login') + '?next=' + reverse('dashboard'))
        self.client.force_login(User.objects.create_user('nobody', password='pw'))
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 403)

    def test_login_page(self):
        make_user('jay', AGENT)
        resp = self.client.post(reverse('dashboard-login'), {'username': 'jay', 'password': 'pw-12345678'})
        self.assertRedirects(resp, reverse('dashboard'), fetch_redirect_response=False)
        self.client.logout()
        self.assertContains(self.client.post(reverse('dashboard-login'), {'username': 'jay', 'password': 'bad'}), 'Wrong username or password')


class TeamTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('boss', password='pw')
        self.client.force_login(self.admin)
        self.url = reverse('dashboard-settings')

    def test_add_member_change_role_and_remove_access(self):
        self.client.post(self.url, {'action': 'add_member', 'username': 'riya', 'first_name': 'Riya', 'password': 'Str0ng-pass!', 'role': AGENT})
        riya = User.objects.get(username='riya')
        self.assertEqual(get_role(riya), AGENT)
        self.client.post(self.url, {'action': 'set_role', 'user_id': riya.pk, 'role': VIEWER})
        self.assertEqual(get_role(User.objects.get(pk=riya.pk)), VIEWER)
        self.client.post(self.url, {'action': 'toggle_active', 'user_id': riya.pk})
        self.assertFalse(User.objects.get(pk=riya.pk).is_active)
        self.assertContains(self.client.get(self.url), 'Riya')

    def test_weak_password_and_self_change_are_refused(self):
        resp = self.client.post(self.url, {'action': 'add_member', 'username': 'x', 'password': '12345678', 'role': AGENT})
        self.assertFalse(User.objects.filter(username='x').exists())
        self.assertContains(resp, 'too common')
        self.client.post(self.url, {'action': 'set_role', 'user_id': self.admin.pk, 'role': VIEWER}, follow=True)
        self.assertEqual(get_role(User.objects.get(pk=self.admin.pk)), ADMIN)


class SentByTests(TestCase):
    @patch('meta_connector.services.message_service.InstagramService.send_instagram_message', return_value=(True, {'message_id': 'mid.1'}, ''))
    def test_reply_records_sender_and_survives_echo_arriving_first(self, _):
        account = PlatformAccount.objects.create(platform='instagram', display_name='IG', instagram_id='IG1')
        contact = Contact.objects.create(platform_account=account, platform_user_id='555')
        Message.objects.create(platform_account=account, contact=contact, message_id='mid.1', direction='outgoing',
                               message_text='hi', raw_payload={'is_echo': True})  # echo saved before our send finished
        user = make_user('jay', AGENT)
        msg, ok, _ = MessageService.send_message('instagram', '555', 'hi', account_id=account.pk, sent_by=user)
        self.assertTrue(ok)
        self.assertEqual(Message.objects.filter(message_id='mid.1').count(), 1)
        self.assertEqual(Message.objects.get(message_id='mid.1').sent_by, user)


class WebhookReliabilityTests(TestCase):
    def setUp(self):
        self.account = PlatformAccount.objects.create(platform='instagram', display_name='IG', instagram_id='IG1')
        self.url = reverse('meta_connector:meta-webhook')

    def dm(self, mid, sender='555', **msg):
        return {'object': 'instagram', 'entry': [{'id': 'IG1', 'messaging': [
            {'sender': {'id': sender}, 'recipient': {'id': 'IG1' if sender != 'IG1' else '555'}, 'message': {'mid': mid, 'text': 'hey', **msg}}]}]}

    @override_settings(META_WEBHOOK_ASYNC=True)
    def test_webhook_answers_first_and_processes_in_background(self):
        with patch('meta_connector.views.webhook_views.process_in_background') as bg:
            with self.captureOnCommitCallbacks(execute=True):
                resp = self.client.post(self.url, json.dumps(self.dm('m1')), content_type='application/json')
        self.assertEqual(resp.status_code, 200)
        bg.assert_called_once_with(WebhookLog.objects.get().id)
        self.assertFalse(Message.objects.exists())  # not processed inline

    def test_failed_and_stuck_logs_are_retried(self):
        failed = WebhookLog.objects.create(platform='instagram', payload=self.dm('m1'), error_message='boom')
        stuck = WebhookLog.objects.create(platform='instagram', payload=self.dm('m2'))
        WebhookLog.objects.filter(pk=stuck.pk).update(created_at=timezone.now() - timedelta(minutes=5))
        fresh = WebhookLog.objects.create(platform='instagram', payload=self.dm('m3'))  # may still be in its thread
        self.assertEqual(set(problem_webhooks().values_list('id', flat=True)), {failed.id, stuck.id})
        self.assertEqual(retry_webhooks(), (2, 0))
        self.assertEqual(set(Message.objects.values_list('message_id', flat=True)), {'m1', 'm2'})
        failed.refresh_from_db()
        self.assertEqual((failed.processed, failed.error_message), (True, ''))
        self.assertFalse(WebhookLog.objects.get(pk=fresh.pk).processed)

    def test_retry_button_is_admin_only_and_banner_shows(self):
        WebhookLog.objects.create(platform='instagram', payload=self.dm('m1'), error_message='boom')
        self.client.force_login(make_user('agent', AGENT))
        self.assertNotContains(self.client.get(reverse('dashboard')), 'not processed')
        self.assertEqual(self.client.post(reverse('dashboard-webhooks'), {'action': 'retry'}).status_code, 403)
        self.client.force_login(User.objects.create_superuser('boss', password='pw'))
        self.assertContains(self.client.get(reverse('dashboard')), '1 webhook not processed')
        resp = self.client.post(reverse('dashboard-webhooks'), {'action': 'retry', 'next': '/dashboard/'}, follow=True)
        self.assertContains(resp, 'Re-processed 1 webhook')
        self.assertTrue(Message.objects.filter(message_id='m1').exists())

    def test_bad_signature_is_counted_and_shown(self):
        MetaConfig.objects.create(app_secret='right-secret')
        body = json.dumps(self.dm('m1')).encode()
        bad = 'sha256=' + hmac.new(b'wrong-secret', body, hashlib.sha256).hexdigest()
        for _ in range(2):
            self.assertEqual(self.client.post(self.url, body, content_type='application/json', HTTP_X_HUB_SIGNATURE_256=bad).status_code, 403)
        self.assertEqual(MetaConfig.get_solo().signature_failures, 2)
        self.assertFalse(WebhookLog.objects.exists())  # untrusted body is not stored
        self.client.force_login(User.objects.create_superuser('boss', password='pw'))
        self.assertContains(self.client.get(reverse('dashboard')), '2 webhooks rejected')
        self.client.post(reverse('dashboard-webhooks'), {'action': 'dismiss_signature'})
        self.assertNotContains(self.client.get(reverse('dashboard')), 'webhooks rejected')

    @override_settings(META_WEBHOOK_ASYNC=False)
    def test_instagram_echo_saved_as_outgoing_once(self):
        echo = self.dm('e1', sender='IG1', is_echo=True)
        self.client.post(self.url, json.dumps(echo), content_type='application/json')
        self.client.post(self.url, json.dumps(echo), content_type='application/json')
        msg = Message.objects.get(message_id='e1')
        self.assertEqual((msg.direction, msg.contact.platform_user_id, msg.raw_payload['is_echo']), ('outgoing', '555', True))
        self.assertEqual(Message.objects.count(), 1)


class CleanupTests(TestCase):
    def test_deletes_old_logs_keeps_recent_and_failed_and_runs_once_a_day(self):
        now = timezone.now()

        def log(days_old, processed=True):
            entry = WebhookLog.objects.create(platform='x', processed=processed)
            WebhookLog.objects.filter(pk=entry.pk).update(created_at=now - timedelta(days=days_old))
            return entry.pk

        old, recent = log(40), log(5)
        old_failed, ancient_failed = log(40, processed=False), log(100, processed=False)
        self.assertEqual(cleanup_webhook_logs(), 2)  # default retention 30 days
        self.assertEqual(set(WebhookLog.objects.values_list('pk', flat=True)), {recent, old_failed})
        log(40)
        self.assertEqual(cleanup_webhook_logs(), 0)  # already ran in the last 24h
        self.assertEqual(cleanup_webhook_logs(force=True), 1)

    def test_role_groups_exist_after_migrate(self):
        self.assertEqual(set(Group.objects.values_list('name', flat=True)), {ADMIN, AGENT, VIEWER})
