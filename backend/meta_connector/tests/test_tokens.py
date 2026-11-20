from datetime import timedelta
from unittest.mock import MagicMock, patch
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from meta_connector.models import MetaConfig, PlatformAccount
from meta_connector.services.instagram_service import InstagramService
from meta_connector.services.token_service import TokenService

IG_SCOPES = ['instagram_basic', 'instagram_manage_messages', 'instagram_manage_comments', 'pages_manage_metadata']
GET = 'meta_connector.services.token_service._get'


def fake_meta(responses):
    """responses: {path: json}. Returns a _get replacement that also records calls."""
    calls = []

    def _get(path, **params):
        calls.append((path, params))
        if path == 'debug_token':
            return {'data': responses['debug'][params['input_token']]}, ''
        data = responses.get(path)
        return (data, '') if data is not None else (None, 'boom')
    return _get, calls


class TokenServiceTests(TestCase):
    def setUp(self):
        self.ig = PlatformAccount.objects.create(platform='instagram', display_name='IG', instagram_id='IG1')

    def test_user_token_becomes_permanent_page_token(self):
        MetaConfig.objects.create(app_secret='secret')
        _get, calls = fake_meta({
            'debug': {
                'short': {'type': 'USER', 'is_valid': True, 'expires_at': 1, 'app_id': 'APP'},
                'page_tok': {'type': 'PAGE', 'is_valid': True, 'expires_at': 0, 'scopes': IG_SCOPES},
            },
            'oauth/access_token': {'access_token': 'long_user'},
            'me/accounts': {'data': [{'name': 'Other', 'access_token': 'x'},
                                     {'name': 'NJ', 'access_token': 'page_tok', 'instagram_business_account': {'id': 'IG1'}}]},
        })
        with patch(GET, _get):
            token, info, note, error = TokenService.prepare('instagram', 'IG1', ' short ')
        self.assertEqual((token, error), ('page_tok', ''))
        self.assertEqual(info['type'], 'PAGE')
        self.assertIn('NJ', note)
        self.assertEqual(calls[1][1]['client_id'], 'APP')            # exchanged with the app from debug_token
        self.assertEqual(calls[2][1]['access_token'], 'long_user')   # pages listed with the long-lived token

    def test_user_token_without_linked_page_is_rejected(self):
        _get, _ = fake_meta({'debug': {'u': {'type': 'USER', 'is_valid': True, 'expires_at': 1}},
                             'me/accounts': {'data': [{'name': 'Shop', 'access_token': 'x'}]}})
        with patch(GET, _get):
            token, _, _, error = TokenService.prepare('instagram', 'IG1', 'u')
        self.assertEqual(token, '')
        self.assertIn('Shop', error)

    def test_invalid_token_is_rejected(self):
        with patch(GET, return_value=(None, 'Error validating access token')):
            _, _, _, error = TokenService.prepare('instagram', 'IG1', 'dead')
        self.assertIn('Error validating access token', error)

    def test_health_states(self):
        acc = self.ig
        self.assertEqual(acc.token_health, 'missing')
        acc.set_access_token('t')
        self.assertEqual(acc.token_health, 'unknown')
        TokenService.apply(acc, {'type': 'PAGE', 'is_valid': True, 'expires_at': 0, 'scopes': IG_SCOPES})
        self.assertEqual((acc.token_health, acc.missing_scopes), ('ok', []))
        acc.token_data_access_expires_at = timezone.now() + timedelta(days=3)
        self.assertEqual(acc.token_health, 'expiring')
        acc.token_expires_at = timezone.now() - timedelta(minutes=1)
        self.assertEqual(acc.token_health, 'expired')
        acc.token_scopes = ['instagram_basic']
        self.assertIn('instagram_manage_comments', acc.missing_scopes)
        TokenService.apply(acc, None, 'Error validating access token: Session has expired')
        self.assertEqual(acc.token_health, 'invalid')

    def test_network_error_keeps_state_unknown_and_is_not_retried_immediately(self):
        self.ig.set_access_token('t')
        self.ig.save()
        with patch(GET, return_value=(None, 'Could not reach Meta: timeout')) as get:
            TokenService.refresh_stale()
            TokenService.refresh_stale()
        self.assertEqual(get.call_count, 1)
        self.ig.refresh_from_db()
        self.assertEqual(self.ig.token_health, 'unknown')

    @patch('meta_connector.services.instagram_service.requests.request')
    def test_live_call_with_dead_token_flags_account(self, request):
        TokenService.apply(self.ig, {'type': 'PAGE', 'is_valid': True, 'scopes': IG_SCOPES})
        self.ig.set_access_token('t')
        self.ig.save()
        request.return_value = MagicMock(status_code=400, json=lambda: {'error': {'code': 190, 'message': 'Session has expired'}})
        ok, _, error = InstagramService.get_profile(self.ig)
        self.assertFalse(ok)
        self.ig.refresh_from_db()
        self.assertEqual((self.ig.token_health, self.ig.token_error), ('invalid', 'Session has expired'))


class TokenUITests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser('s', password='p'))
        self.acc = PlatformAccount.objects.create(platform='instagram', display_name='Shop IG', instagram_id='IG1')
        self.acc.set_access_token('t')
        TokenService.apply(self.acc, None, 'Error validating access token: Session has expired')
        self.acc.save()

    def test_dashboard_and_settings_show_alert(self):
        for url in (reverse('dashboard'), reverse('dashboard-settings')):
            resp = self.client.get(url)
            self.assertContains(resp, 'Access token needs attention')
            self.assertContains(resp, 'Session has expired')

    @patch(GET, return_value=({'data': {'type': 'PAGE', 'is_valid': True, 'expires_at': 0, 'scopes': IG_SCOPES}}, ''))
    def test_check_token_button(self, _):
        resp = self.client.post(reverse('dashboard-settings'), {'action': 'check_token', 'account_id': self.acc.pk}, follow=True)
        self.assertContains(resp, 'checked: OK')
        self.assertNotContains(resp, 'Access token needs attention')
