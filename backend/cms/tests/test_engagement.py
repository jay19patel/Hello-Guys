from unittest.mock import patch

from django.core.cache import cache
from django.test import TestCase
from rest_framework.test import APIClient
from rest_framework.throttling import ScopedRateThrottle

from cms.models import BlogComment, BlogLike, BlogPost


class EngagementTests(TestCase):
    def setUp(self):
        cache.clear()  # throttle counters live in the cache
        self.post = BlogPost.objects.create(title='Hello', slug='hello', is_published=True)
        self.url = '/api/cms/blog/hello/engagement/'
        self.client = APIClient()

    def test_like_toggles_and_forged_forwarded_for_is_ignored(self):
        self.assertTrue(self.client.post(self.url, {'action': 'like'}, format='json').json()['liked'])
        # NUM_PROXIES=0: a spoofed header must not create a different visitor.
        resp = self.client.post(self.url, {'action': 'like'}, format='json', HTTP_X_FORWARDED_FOR='9.9.9.9')
        self.assertFalse(resp.json()['liked'])
        self.assertEqual(BlogLike.objects.count(), 0)

    def test_comment_author_validated(self):
        bad = self.client.post(self.url, {'action': 'comment', 'text': 'hi', 'author': 'x' * 101}, format='json')
        self.assertEqual(bad.status_code, 400)
        ok = self.client.post(self.url, {'action': 'comment', 'text': 'hi', 'author': '   '}, format='json')
        self.assertEqual(ok.status_code, 200)
        self.assertTrue(BlogComment.objects.get().author_name.startswith('Guest-'))

    def test_writes_are_throttled(self):
        with patch.dict(ScopedRateThrottle.THROTTLE_RATES, {'engagement': '2/hour'}):
            codes = [self.client.post(self.url, {'action': 'like'}, format='json').status_code for _ in range(3)]
        self.assertEqual(codes, [200, 200, 429])
