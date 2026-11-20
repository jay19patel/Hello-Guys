from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from meta_connector.models import PlatformAccount
from cms.models import InquirySubmission, Project


class HubPortalTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='portal_user',
            email='portal@example.com',
            password='Password123!',
        )

    def test_unauthenticated_user_redirected_to_login(self):
        response = self.client.get(reverse('hub-portal'))
        self.assertEqual(response.status_code, 302)
        self.assertIn('/dashboard/login/', response.url)

    def test_authenticated_user_portal_view(self):
        self.client.login(username='portal_user', password='Password123!')
        
        # Create minimal records in both modules
        PlatformAccount.objects.create(
            platform=PlatformAccount.PLATFORM_WHATSAPP,
            display_name='WhatsApp Support',
            phone_number_id='wa_123',
            is_active=True,
        )
        InquirySubmission.objects.create(
            name='Lead John',
            email='john@example.com',
            subject='Project Inquiry',
            message='I need a website',
            is_replied=False,
        )
        Project.objects.create(
            title='NJTechStudio Portal',
            slug='njtechstudio-portal',
            description='Web portal',
            is_active=True,
            featured=True,
        )

        response = self.client.get(reverse('hub-portal'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Choose a Workspace Module')
        self.assertContains(response, 'Meta Connector')
        self.assertContains(response, 'CMS Studio')
        self.assertContains(response, '/dashboard/')
        self.assertContains(response, '/cms/')
        self.assertContains(response, '1 Pending')
