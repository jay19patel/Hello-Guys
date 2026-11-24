from .base import *

# Production settings
DEBUG = False

# Required: the insecure development default from base.py must never reach production.
SECRET_KEY = env('SECRET_KEY')
FIELD_ENCRYPTION_KEY = env('FIELD_ENCRYPTION_KEY')

# Shared cache: rate limits and the AI trigger index must be the same for every worker process.
CACHES = {
    'default': {
        'BACKEND': 'django.core.cache.backends.redis.RedisCache',
        'LOCATION': env('REDIS_URL', default='redis://localhost:6379/0'),
    }
}

ALLOWED_HOSTS = env.list('ALLOWED_HOSTS')

# Database: PostgreSQL in production via DATABASE_URL
DATABASES = {
    'default': env.db('DATABASE_URL')
}

# Production Security Hardening
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
# Rate limits read the client IP from X-Forwarded-For, set by this many proxies (nginx = 1).
# Unset, a visitor could send a fake header on every request and never be throttled.
NUM_PROXIES = env.int('NUM_PROXIES', default=1)
REST_FRAMEWORK['NUM_PROXIES'] = NINJA_NUM_PROXIES = NUM_PROXIES
REST_FRAMEWORK['DEFAULT_RENDERER_CLASSES'] = ['rest_framework.renderers.JSONRenderer']
META_REQUIRE_SIGNATURE = True
SECURE_SSL_REDIRECT = env.bool('SECURE_SSL_REDIRECT', default=True)
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SECURE_BROWSER_XSS_FILTER = True
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = 'DENY'

SECURE_HSTS_SECONDS = env.int('SECURE_HSTS_SECONDS', default=31536000)  # 1 year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
