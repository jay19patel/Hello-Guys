from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from meta_connector.models import PlatformAccount, Contact, Message


class UnifiedDashboardTests(TestCase):
    def setUp(self):
        self.client.force_login(User.objects.create_superuser('staff', password='pw'))
        self.account = PlatformAccount.objects.create(
            platform=PlatformAccount.PLATFORM_WHATSAPP,
            display_name="Test Channel",
            phone_number_id="123456789",
            is_active=True
        )
        self.contact = Contact.objects.create(
            platform_account=self.account,
            platform_user_id="919876543210",
            name="Alice"
        )
        self.message = Message.objects.create(
            platform_account=self.account,
            contact=self.contact,
            message_id="wamid.test_msg_1",
            direction=Message.DIRECTION_INCOMING,
            message_text="Hello world"
        )

    def test_dashboard_ui_renders_successfully(self):
        """Verifies GET /dashboard/ renders HTTP 200 with dashboard/index.html."""
        url = reverse('dashboard')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'dashboard/index.html')
        self.assertContains(response, "Hello Guys")
        self.assertContains(response, "Unified Contact Directory &amp; Messaging")
        self.assertContains(response, "Hello world")

    def test_dashboard_api_overview(self):
        """Verifies GET /api/meta/dashboard/api/overview/ returns JSON metrics."""
        url = reverse('meta_connector:dashboard-overview')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['status'], 'ok')
        self.assertEqual(response.json()['metrics']['total_contacts'], 1)

    def test_live_version_changes_on_new_message(self):
        """The dashboard's live poll only re-renders when this version changes."""
        url = reverse('meta_connector:dashboard-overview')
        before = self.client.get(url).json()['version']
        Message.objects.create(
            platform_account=self.account,
            contact=self.contact,
            message_id="wamid.test_msg_2",
            direction=Message.DIRECTION_INCOMING,
            message_text="Second message"
        )
        after = self.client.get(url).json()['version']
        self.assertNotEqual(before, after)

    def test_dynamic_platform_groups_in_sidebar(self):
        """Verifies WhatsApp, Instagram and Facebook Messenger appear automatically in sidebar."""
        PlatformAccount.objects.create(
            platform=PlatformAccount.PLATFORM_INSTAGRAM,
            display_name="Brand Instagram",
            instagram_id="IG_123",
            is_active=True
        )
        PlatformAccount.objects.create(
            platform=PlatformAccount.PLATFORM_FACEBOOK,
            display_name="Support Messenger",
            instagram_id="PAGE_123",
            is_active=True
        )
        response = self.client.get(reverse('dashboard'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Test Channel")
        self.assertContains(response, "Brand Instagram")
        self.assertContains(response, "Support Messenger")
        self.assertContains(response, "Facebook Messenger")

    def test_dashboard_marks_live_sections(self):
        response = self.client.get(reverse('dashboard'))
        for section in ('metrics', 'contacts', 'messages'):
            self.assertContains(response, f'data-live="{section}"')

    def test_dashboard_requires_staff_login(self):
        self.client.logout()
        for url in (reverse('dashboard'), reverse('meta_connector:dashboard-overview')):
            self.assertEqual(self.client.get(url).status_code, 302)
        self.client.force_login(User.objects.create_user('normal', password='pw'))  # logged in, but no role
        self.assertEqual(self.client.get(reverse('dashboard')).status_code, 403)


class ContactPageTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_superuser('staff', password='pw')
        self.client.force_login(self.user)
        self.account = PlatformAccount.objects.create(platform='instagram', display_name='IG', instagram_id='IG1')
        self.contact = Contact.objects.create(platform_account=self.account, platform_user_id='555', username='fan_1', name='Riya Shah')
        Message.objects.create(platform_account=self.account, contact=self.contact, message_id='c1',
                               message_type='comment', message_text='Price please?')
        Message.objects.create(platform_account=self.account, contact=self.contact, message_id='m1', message_text='Hi there')
        Message.objects.create(platform_account=self.account, contact=self.contact, message_id='o1',
                               direction='outgoing', message_text='Hello Riya', status='failed')
        self.url = reverse('dashboard-contact', args=[self.contact.pk])

    def test_overview_links_to_contact_page(self):
        self.assertContains(self.client.get(reverse('dashboard')), self.url)

    def test_page_shows_conversation_counts_and_window(self):
        resp = self.client.get(self.url)
        for text in ('Riya Shah', '@fan_1', 'Commented on a post', 'Price please?', 'Hi there', 'Hello Riya',
                     'Not delivered', 'Reply window open', 'data-live="conversation"'):
            self.assertContains(resp, text)
        self.assertEqual(resp.context['counts'], {'incoming': 1, 'outgoing': 1, 'comments': 1})

    def test_reply_sends_through_message_service(self):
        from unittest.mock import patch
        with patch('meta_connector.views.contact_views.MessageService.send_message', return_value=(None, True, '')) as send:
            resp = self.client.post(self.url, {'text': '  Thanks!  '}, follow=True)
        send.assert_called_once_with(platform='instagram', to='555', text='Thanks!', account_id=self.account.pk, sent_by=self.user)
        self.assertContains(resp, 'Message sent.')

        with patch('meta_connector.views.contact_views.MessageService.send_message', return_value=(None, False, 'Outside window')):
            self.assertContains(self.client.post(self.url, {'text': 'x'}, follow=True), 'Meta said: Outside window')
        self.assertContains(self.client.post(self.url, {'text': '  '}, follow=True), 'Type a message first.')

    def test_long_chat_shows_newest_messages_oldest_first(self):
        Message.objects.bulk_create([Message(platform_account=self.account, contact=self.contact, message_id=f'bulk{i}',
                                             message_text=f'bulk {i}') for i in range(510)])
        convo = self.client.get(self.url).context['conversation']
        self.assertEqual(len(convo), 500)
        self.assertNotIn('c1', [m.message_id for m in convo])  # oldest messages drop off, not the newest
        self.assertLessEqual(convo[0].created_at, convo[-1].created_at)

    def test_composer_locks_when_reply_window_closed(self):
        from datetime import timedelta
        from django.utils import timezone
        Message.objects.filter(contact=self.contact).update(created_at=timezone.now() - timedelta(hours=25))
        resp = self.client.get(self.url)
        self.assertContains(resp, 'Reply window closed')
        self.assertContains(resp, 'aria-describedby="windowNote"')

    def test_requires_staff(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
