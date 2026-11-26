import os
from pathlib import Path
import environ

# Build paths inside the project like this: BASE_DIR / 'subdir'.
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# Set LiteLLM to use local model cost map fallback without attempting external downloads
os.environ.setdefault("LITELLM_LOCAL_MODEL_COST_MAP", "True")

# Initialize environment variables
env = environ.Env()

# Look for .env file in project directory or parent directory
env_file = BASE_DIR / '.env'
if not env_file.exists():
    env_file = BASE_DIR.parent / '.env'
if env_file.exists():
    environ.Env.read_env(env_file)

# SECRET_KEY: local.py has a dev-only default; production.py requires the env var.

# Application definition
INSTALLED_APPS = [
    # hello_guy_ai MUST be before django.contrib.admin
    # so its admin/base_site.html override (AI Chat link) loads first.
    'hello_guy_ai.apps.HelloGuyAiConfig',

    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.humanize',

    # Third-party apps
    'corsheaders',
    'rest_framework',
    'rest_framework.authtoken',

    # Local apps
    'core.apps.CoreConfig',
    'meta_connector.apps.MetaConnectorConfig',
    'cms.apps.CmsConfig',
    'crm.apps.CrmConfig',
    'pms.apps.PmsConfig',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'corsheaders.middleware.CorsMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

LOGIN_URL = 'dashboard-login'
LOGIN_REDIRECT_URL = 'dashboard'
LOGOUT_REDIRECT_URL = 'dashboard-login'

# Full origins allowed to POST forms, e.g. https://example.com (comma-separated in .env)
CSRF_TRUSTED_ORIGINS = env.list('CSRF_TRUSTED_ORIGINS', default=[
    'https://njtechstudio.in',
    'https://www.njtechstudio.in',
])

# CORS configuration for Next.js frontend
CORS_ALLOWED_ORIGINS = env.list('CORS_ALLOWED_ORIGINS', default=[
    'https://njtechstudio.in',
    'https://www.njtechstudio.in',
])
CORS_ALLOW_ALL_ORIGINS = env.bool('CORS_ALLOW_ALL_ORIGINS', default=False)  # only CORS_ALLOWED_ORIGINS

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'meta_connector.context_processors.dashboard_nav',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'
ASGI_APPLICATION = 'config.asgi.application'

# Password validation
AUTH_PASSWORD_VALIDATORS = [
    {
        'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator',
    },
    {
        'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator',
    },
]

# Internationalization
LANGUAGE_CODE = 'en-us'
TIME_ZONE = env('TIME_ZONE', default='Asia/Kolkata')  # used for 'daily' charts and every time shown in the UI
USE_I18N = True
USE_TZ = True

# Static & Media files
STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_URL = '/media/'
MEDIA_ROOT = BASE_DIR / 'media'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# Django REST Framework Settings
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': [
        'rest_framework.authentication.TokenAuthentication',
        'rest_framework.authentication.SessionAuthentication',
    ],
    'DEFAULT_PERMISSION_CLASSES': [
        'rest_framework.permissions.IsAuthenticated',
    ],
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 20,
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
        'rest_framework.renderers.BrowsableAPIRenderer',
    ],
    # Public writes and the token endpoint opt in per view with throttle_scope (shared cache in production).
    'DEFAULT_THROTTLE_RATES': {
        'inquiry': env('THROTTLE_INQUIRY', default='5/hour'),
        'engagement': env('THROTTLE_ENGAGEMENT', default='30/hour'),
        'auth': env('THROTTLE_AUTH', default='10/minute'),
    },
}

# Client IP for rate limits = REMOTE_ADDR, or the X-Forwarded-For entry added by this many trusted proxies.
# 0 trusts no header (direct traffic, local dev); production sits behind nginx (1).
NUM_PROXIES = env.int('NUM_PROXIES', default=0)
REST_FRAMEWORK['NUM_PROXIES'] = NINJA_NUM_PROXIES = NUM_PROXIES
NINJA_PAGINATION_MAX_LIMIT = 100

# Database - Simple SQLite
DATABASES = {
    'default': env.db('DATABASE_URL', default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")
}

# Meta app settings, accounts and tokens are configured from the UI: /dashboard/settings/
# Encryption key for stored tokens stays outside the DB it protects.
FIELD_ENCRYPTION_KEY = env('FIELD_ENCRYPTION_KEY', default='')
# Answer Meta's webhook at once and process it in a background thread (failed/stuck ones can be retried from the dashboard).
META_WEBHOOK_ASYNC = env.bool('META_WEBHOOK_ASYNC', default=True)
# Reject webhook POSTs while no App Secret is configured (production always; local can test unsigned payloads).
META_REQUIRE_SIGNATURE = env.bool('META_REQUIRE_SIGNATURE', default=False)

# Logging Configuration
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '[{asctime}] [{levelname}] [{name}:{lineno}] {message}',
            'style': '{',
        },
        'simple': {
            'format': '[{levelname}] {name}: {message}',
            'style': '{',
        },
    },
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
    },
    'root': {
        'handlers': ['console'],
        'level': env('DJANGO_LOG_LEVEL', default='INFO'),
    },
    'loggers': {
        'meta_connector': {
            'handlers': ['console'],
            'level': env('DJANGO_LOG_LEVEL', default='INFO'),
            'propagate': False,
        },
        # LiteLLM logs every request at INFO; keep warnings and errors only.
        'LiteLLM': {'level': 'WARNING'},
        'django.request': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
    },
}

# Hello Guy AI: models the generic db_* tools may touch (writes still need approval + Django perms).
# Reads go through the explicit tools in cms/ai_tools.py.
HELLO_GUY_AI = {
    "ORM_TOOLS_MODELS": [
        "cms.blogpost",
        "cms.project",
        "cms.faq",
        "cms.testimonial",
        "cms.solution",
        "cms.techskill",
        "cms.youtubevideo",
        "crm.opportunity",
        "crm.customer",
        "crm.meeting",
    ],
    "REDIS_URL": env('REDIS_URL', default='redis://localhost:6379/0'),
    "LLM_FALLBACK_MODELS": env.list('LLM_FALLBACK_MODELS', default=[]),
    "DAILY_TOKEN_LIMIT_PER_USER": env.int('AI_DAILY_TOKEN_LIMIT_PER_USER', default=0),
    "NOTIFY_EMAIL": env.bool('AI_NOTIFY_EMAIL', default=False),
    "RETENTION_DAYS": env.int('AI_RETENTION_DAYS', default=0),
    "PUBLIC_AGENT": "website_assistant",
    "PUBLIC_USER": "website-bot",
}
