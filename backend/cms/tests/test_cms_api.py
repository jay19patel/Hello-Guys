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
    WorkflowStep,
    AboutProfile,
    AboutStat,
    AboutBlock,
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

    def test_workflow_steps_api(self):
        WorkflowStep.objects.create(
            step_num='01',
            title='Architecture Blueprint',
            tag='Design',
            description='System design',
            icon='GitBranch',
            order=1,
        )
        response = self.client.get(reverse('cms:workflow-step-list'))
        self.assertEqual(response.status_code, 200)
        self.assertGreaterEqual(len(response.data), 1)
        self.assertEqual(response.data[0]['step_num'], '01')

    def test_about_content_api(self):
        profile = AboutProfile.get_solo()
        profile.studio_title = 'Studio Test Title'
        profile.save()

        AboutStat.objects.create(
            value='5',
            suffix='+ Years',
            label='Experience',
            is_counter=True,
            is_dark=True,
            order=1,
        )

        AboutBlock.objects.create(
            badge='Culture',
            title='Engineering Excellence',
            content='We write clean code.',
            col_span='6',
            order=1,
        )

        response = self.client.get(reverse('cms:about-content'))
        self.assertEqual(response.status_code, 200)
        self.assertIn('profile', response.data)
        self.assertIn('stats', response.data)
        self.assertIn('blocks', response.data)
        self.assertEqual(response.data['profile']['studio_title'], 'Studio Test Title')
        self.assertGreaterEqual(len(response.data['stats']), 1)
        self.assertGreaterEqual(len(response.data['blocks']), 1)

    def test_api_toggle_maintenance_mode(self):
        self.site_settings.api_workflow_enabled = False
        self.site_settings.save()

        response = self.client.get(reverse('cms:workflow-step-list'))
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.data['error'], 'api_disabled')
        self.assertEqual(response.data['endpoint'], 'workflow')

