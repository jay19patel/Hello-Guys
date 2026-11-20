from unittest.mock import patch
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from meta_connector.models import MetaConfig, PlatformAccount


class SettingsUITests(TestCase):
    def setUp(self):
        self.url = reverse('dashboard-settings')
        self.staff = User.objects.create_superuser('admin', password='pw')

    def test_requires_staff_login(self):
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_save_config_and_verify_webhook_with_it(self):
        self.client.force_login(self.staff)
        resp = self.client.post(self.url, {
            'action': 'config', 'app_name': 'X', 'verify_token': 'ui_token',
            'graph_api_version': 'v22.0', 'webhook_log_retention_days': 30, 'new_app_secret': 's3cret',
        })
        self.assertEqual(resp.status_code, 302)
        config = MetaConfig.get_solo()
        self.assertEqual((config.verify_token, config.app_secret), ('ui_token', 's3cret'))
        self.assertEqual(config.graph_base_url, 'https://graph.facebook.com/v22.0')

        # blank secret keeps the saved one
        self.client.post(self.url, {'action': 'config', 'app_name': 'X', 'verify_token': 'ui_token', 'graph_api_version': 'v22.0', 'webhook_log_retention_days': 30})
        self.assertEqual(MetaConfig.get_solo().app_secret, 's3cret')

        resp = self.client.get(reverse('meta_connector:meta-webhook'), {
            'hub.mode': 'subscribe', 'hub.verify_token': 'ui_token', 'hub.challenge': 'abc'})
        self.assertEqual(resp.content, b'abc')

    @patch('meta_connector.services.token_service.TokenService.prepare',
           side_effect=lambda platform, ig_id, raw: (raw, {'type': 'PAGE', 'is_valid': True, 'expires_at': 0}, '', ''))
    def test_add_edit_delete_account(self, prepare):
        self.client.force_login(self.staff)
        # new account needs its ID and token
        resp = self.client.post(self.url, {'action': 'account', 'platform': 'whatsapp', 'display_name': 'WA', 'is_active': 'on'})
        self.assertEqual(resp.status_code, 200)
        self.assertFalse(PlatformAccount.objects.exists())

        self.client.post(self.url, {'action': 'account', 'platform': 'whatsapp', 'display_name': 'WA',
                                    'phone_number_id': '123', 'raw_access_token': 'tok', 'is_active': 'on'})
        acc = PlatformAccount.objects.get()
        self.assertEqual(acc.get_access_token(), 'tok')

        # edit without token keeps it
        self.client.post(self.url, {'action': 'account', 'account_id': acc.pk, 'platform': 'whatsapp',
                                    'display_name': 'WA2', 'phone_number_id': '123', 'is_active': 'on'})
        acc.refresh_from_db()
        self.assertEqual((acc.display_name, acc.get_access_token()), ('WA2', 'tok'))

        self.client.post(self.url, {'action': 'delete', 'account_id': acc.pk})
        self.assertFalse(PlatformAccount.objects.exists())
