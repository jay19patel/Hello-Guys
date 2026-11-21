from django.test import TestCase, Client
from django.contrib.auth.models import User
from django.urls import reverse
from cms.models import (
    InquirySubmission,
    Project,
    SiteSettings,
)


class CMSUITests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(
            username='admin_tester',
            email='admin@test.com',
            password='Password123!',
            is_staff=True,
            is_superuser=True,
        )
        self.client.login(username='admin_tester', password='Password123!')

        self.inquiry = InquirySubmission.objects.create(
            name='Test Contact',
            email='contact@test.com',
            phone='+1 555-0199',
            subject='Project Inquiry',
            message='Need a CRM system',
            is_replied=False,
        )

    def test_overview_renders_successfully(self):
        response = self.client.get(reverse('cms_ui:overview'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'CMS Studio')
        self.assertContains(response, 'Inquiries')
        self.assertContains(response, 'Test Contact')

    def test_inquiry_list_view(self):
        response = self.client.get(reverse('cms_ui:inquiry-list'))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Test Contact')
        self.assertContains(response, 'contact@test.com')

    def test_inquiry_list_filters_unreplied(self):
        response = self.client.get(reverse('cms_ui:inquiry-list') + '?status=unreplied')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Test Contact')

        # Filter by replied -> should not contain Test Contact
        response_replied = self.client.get(reverse('cms_ui:inquiry-list') + '?status=replied')
        self.assertEqual(response_replied.status_code, 200)
        self.assertNotContains(response_replied, 'contact@test.com')

    def test_inquiry_create_view(self):
        url = reverse('cms_ui:inquiry-create')
        data = {
            'name': 'New Client Lead',
            'email': 'newclient@example.com',
            'phone': '+91 91234 56789',
            'subject': 'Enterprise AI Solution',
            'inquiry_type': 'Contact Form',
            'message': 'We want to automate incoming WhatsApp leads with AI.',
            'is_replied': False,
        }
        response = self.client.post(url, data=data)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(InquirySubmission.objects.filter(email='newclient@example.com').exists())

    def test_inquiry_detail_and_add_note(self):
        url = reverse('cms_ui:inquiry-detail', kwargs={'pk': self.inquiry.pk})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Need a CRM system')

        # Post an internal note
        post_response = self.client.post(url, data={
            'action': 'add_note',
            'note': 'Called client via phone, demo booked for Monday.',
        })
        self.assertEqual(post_response.status_code, 302)
        self.inquiry.refresh_from_db()
        self.assertTrue(self.inquiry.is_replied)
        self.assertEqual(len(self.inquiry.replies), 1)
        self.assertIn('Called client via phone', self.inquiry.replies[0]['note'])

    def test_inquiry_toggle_replied_action(self):
        url = reverse('cms_ui:inquiry-toggle-replied', kwargs={'pk': self.inquiry.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, 302)
        self.inquiry.refresh_from_db()
        self.assertTrue(self.inquiry.is_replied)

        # Toggle back
        self.client.post(url)
        self.inquiry.refresh_from_db()
        self.assertFalse(self.inquiry.is_replied)

    def test_project_crud_views(self):
        create_url = reverse('cms_ui:project-create')
        proj_data = {
            'title': 'Awesome Cloud ERP',
            'slug': 'awesome-cloud-erp',
            'category': 'Web Application',
            'subtitle': 'Fast enterprise ERP',
            'description': 'Built for manufacturing operations.',
            'technologies_input': 'Django, Vue, PostgreSQL',
            'status': 'Live',
            'client': 'Nexus Corp',
            'order': 1,
            'is_active': True,
        }
        res = self.client.post(create_url, data=proj_data)
        self.assertEqual(res.status_code, 302)
        self.assertTrue(Project.objects.filter(slug='awesome-cloud-erp').exists())

        # List view
        list_res = self.client.get(reverse('cms_ui:project-list'))
        self.assertEqual(list_res.status_code, 200)
        self.assertContains(list_res, 'Awesome Cloud ERP')

    def test_site_settings_view(self):
        settings_url = reverse('cms_ui:site-settings')
        response = self.client.get(settings_url)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Website Settings & Profile')

        post_data = {
            'brand_name': 'NJTechStudio Pro',
            'tagline': 'High-performance AI Automation',
            'bio': 'Senior Software Architect',
            'avatar_url': '/avatar.png',
            'contact_email': 'hello@njtechstudio.in',
            'phone_number': '+91 99999 88888',
            'location': 'Gujarat, India',
            'github_url': 'https://github.com/jay19patel',
            'linkedin_url': 'https://linkedin.com/in/jay-patel',
            'youtube_url': 'https://youtube.com/@njtechstudio',
            'twitter_url': 'https://x.com/jaypatel',
            'resume_url': '/resume.pdf',
            'audio_track_url': '/track.mp3',
            'meta_title': 'NJTechStudio | Elite Web & AI Studio',
            'meta_description': 'Enterprise Web Development and AI Workflows.',
        }
        update_res = self.client.post(settings_url, data=post_data)
        self.assertEqual(update_res.status_code, 302)
        settings_obj = SiteSettings.get_solo()
        self.assertEqual(settings_obj.brand_name, 'NJTechStudio Pro')
        self.assertEqual(settings_obj.contact_email, 'hello@njtechstudio.in')

