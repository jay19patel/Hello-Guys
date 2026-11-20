from django.test import TestCase
from django.urls import reverse
from rest_framework.test import APIClient
from cms.models import (
    SiteSettings,
    Solution,
    Project,
    TechSkill,
    Testimonial,
    FAQ,
    YouTubeVideo,
    BlogPost,
    InquirySubmission,
)


class CMSAPITests(TestCase):
    def setUp(self):
        self.client = APIClient()

        # Seed minimal data
        self.site_settings = SiteSettings.get_solo()
        self.site_settings.brand_name = 'NJTechStudio Test'
        self.site_settings.save()

        self.solution = Solution.objects.create(
            num='01',
            tag='Fintech',
            title='Trading Engine',
            slug='trading-engine',
            description='Low-latency trading',
            order=0,
        )

        self.project = Project.objects.create(
            title='Trade Buddy',
            slug='trade-buddy',
            description='Financial dashboard',
            category='Fintech',
            featured=True,
            order=0,
        )

        self.skill = TechSkill.objects.create(
            name='Python',
            category=TechSkill.CATEGORY_BACKEND,
            proficiency_order=1,
        )

        self.testimonial = Testimonial.objects.create(
            name='John Doe',
            role='Founder, FinCorp',
            text='Superb execution and delivery.',
            rating=5,
        )

        self.faq = FAQ.objects.create(
            question='What is the turnaround time?',
            answer='Typically 2-4 weeks.',
            category='Process',
        )

        self.video = YouTubeVideo.objects.create(
            video_id='dQw4w9WgXcQ',
            title='Next.js Tutorial',
            badge='TUTORIAL',
        )

        self.blog = BlogPost.objects.create(
            title='Building High-Speed Webhook Engines',
            slug='building-high-speed-webhook-engines',
            content='# Markdown Content\nHere is how to build webhooks.',
            category='Architecture',
            is_published=True,
        )

    def test_site_config_api(self):
        response = self.client.get(reverse('cms:site-config'))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data['brand_name'], 'NJTechStudio Test')

    def test_home_overview_api(self):
        response = self.client.get(reverse('cms:home-overview'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('site', response.data)
        self.assertIn('solutions', response.data)
        self.assertIn('featured_projects', response.data)
        self.assertIn('tools', response.data)
        self.assertIn('testimonials', response.data)
        self.assertIn('faqs', response.data)

    def test_solutions_api(self):
        response = self.client.get(reverse('cms:solution-list'))
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['slug'], 'trading-engine')

    def test_projects_api_and_detail(self):
        list_res = self.client.get(reverse('cms:project-list'))
        self.assertEqual(list_res.status_code, 200)
        self.assertGreaterEqual(len(list_res.data), 1)

        detail_res = self.client.get(reverse('cms:project-detail', kwargs={'slug': 'trade-buddy'}))
        self.assertEqual(detail_res.status_code, 200)
        self.assertEqual(detail_res.data['title'], 'Trade Buddy')

    def test_skills_api(self):
        response = self.client.get(reverse('cms:skill-list') + '?grouped=1')
        self.assertEqual(response.status_code, 200)
        self.assertIn('backend', response.data)

    def test_testimonials_api(self):
        response = self.client.get(reverse('cms:testimonial-list'))
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.data), 1)

    def test_faqs_api(self):
        response = self.client.get(reverse('cms:faq-list'))
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.data), 1)

    def test_videos_api(self):
        response = self.client.get(reverse('cms:video-list'))
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.data), 1)

    def test_blog_api_and_like(self):
        list_res = self.client.get(reverse('cms:blog-list'))
        self.assertEqual(list_res.status_code, 200)
        self.assertGreaterEqual(len(list_res.data), 1)

        detail_res = self.client.get(reverse('cms:blog-detail', kwargs={'slug': 'building-high-speed-webhook-engines'}))
        self.assertEqual(detail_res.status_code, 200)
        self.assertEqual(detail_res.data['views_count'], 1)  # Incremented views

        # Like
        like_res = self.client.post(reverse('cms:blog-like', kwargs={'slug': 'building-high-speed-webhook-engines'}))
        self.assertEqual(like_res.status_code, 200)
        self.assertEqual(like_res.data['likes_count'], 1)

    def test_inquiry_create_api(self):
        payload = {
            'name': 'API Lead',
            'email': 'api.lead@example.com',
            'phone': '+1 555-0987',
            'subject': 'Website Revamp Inquiry',
            'message': 'We need custom API integrations for our CRM.',
            'inquiry_type': 'Project Inquiry Modal',
        }
        response = self.client.post(reverse('cms:inquiry-create'), data=payload, format='json')
        self.assertEqual(response.status_code, 201)
        self.assertTrue(InquirySubmission.objects.filter(email='api.lead@example.com').exists())
