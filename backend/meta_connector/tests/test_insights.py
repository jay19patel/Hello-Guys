from datetime import timedelta
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from meta_connector.models import AutoDMLog, Contact, Message, PlatformAccount


class InsightsTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser('s', password='p'))
        self.ig = PlatformAccount.objects.create(platform='instagram', display_name='Shop IG', instagram_id='1')
        self.wa = PlatformAccount.objects.create(platform='whatsapp', display_name='Support WA', phone_number_id='2')
        self.c_ig = Contact.objects.create(platform_account=self.ig, platform_user_id='a', name='Asha')
        self.c_wa = Contact.objects.create(platform_account=self.wa, platform_user_id='b', name='Bilal')
        n = 0

        def msg(cust, days_ago, **kw):
            nonlocal n
            n += 1
            m = Message.objects.create(platform_account=cust.platform_account, contact=cust, message_id=f'x{n}', **kw)
            Message.objects.filter(pk=m.pk).update(created_at=timezone.now() - timedelta(days=days_ago))

        for _ in range(3):
            msg(self.c_ig, 0, message_text='hi')                                  # 3 IG DMs today
        msg(self.c_ig, 0, message_type='comment', message_text='price?')        # 1 IG comment
        msg(self.c_wa, 1, message_text='hello')                                 # 1 WA DM yesterday
        msg(self.c_wa, 1, direction='outgoing', message_text='hey')             # 1 WA reply
        msg(self.c_ig, 10, message_text='old')                                  # 10 days ago: in 30d, not in 7d prev? (prev of 7d)
        AutoDMLog.objects.create(platform_account=self.ig, contact=self.c_ig, media_id='M', comment_id='c', status='sent')

    def test_numbers_by_day_channel_and_account(self):
        resp = self.client.get(reverse('dashboard') + '?days=7')
        ctx = resp.context
        self.assertEqual(len(ctx['daily']['days']), 7)
        self.assertEqual(ctx['daily']['instagram'][-1], 4)
        self.assertEqual(ctx['daily']['whatsapp'][-2], 2)
        self.assertEqual(ctx['totals_by_channel'], {'instagram': 4, 'whatsapp': 2})
        kpi = {k['label']: k for k in ctx['kpis']}
        self.assertEqual((kpi['Messages']['value'], kpi['Received DMs']['value'], kpi['Replies sent']['value'], kpi['Comments']['value']), (6, 4, 1, 1))
        self.assertEqual(kpi['Messages']['delta'], 500)  # 1 message in the 7 days before -> 6 now
        self.assertEqual(kpi['Auto-DMs sent']['value'], 1)
        accounts = {a.display_name: a for a in ctx['accounts']}
        self.assertEqual((accounts['Shop IG'].total, accounts['Support WA'].sent), (4, 1))
        self.assertEqual(sum(map(sum, ctx['heatmap']['grid'])), 5)  # incoming only
        self.assertContains(resp, 'Instagram brings 67% of your messages.')
        self.assertContains(resp, 'Messages are up 500%')

    def test_range_param_and_empty_state(self):
        Message.objects.all().delete()
        resp = self.client.get(reverse('dashboard') + '?days=999')  # invalid -> 30
        self.assertEqual(resp.context['days'], 30)
        self.assertContains(resp, 'No messages in the last 30 days yet')

    def test_requires_staff(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 302)

    def test_old_insights_url_redirects_to_overview_with_range(self):
        resp = self.client.get(reverse('dashboard-insights') + '?days=7')
        self.assertRedirects(resp, reverse('dashboard') + '?days=7', fetch_redirect_response=False)
