from .base import *

# Simple Local Development Settings
DEBUG = True
ALLOWED_HOSTS = ['*']

# ngrok tunnels for testing Meta webhooks / the dashboard over HTTPS
CSRF_TRUSTED_ORIGINS += ['https://*.ngrok-free.dev', 'https://*.ngrok-free.app', 'https://*.ngrok.io']
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')  # so the Callback URL shows https://

# Simple SQLite Database
DATABASES = {
    'default': env.db('DATABASE_URL', default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")
}
