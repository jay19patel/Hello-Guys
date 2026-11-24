from .base import *

# Simple Local Development Settings
DEBUG = True
ALLOWED_HOSTS = ['*']
SECRET_KEY = env('SECRET_KEY', default='django-insecure-local-development-only')

# Next.js dev server
CSRF_TRUSTED_ORIGINS += ['http://localhost:3000', 'http://127.0.0.1:3000']
CORS_ALLOWED_ORIGINS += ['http://localhost:3000', 'http://127.0.0.1:3000']

# ngrok tunnels for testing Meta webhooks / the dashboard over HTTPS
CSRF_TRUSTED_ORIGINS += ['https://*.ngrok-free.dev', 'https://*.ngrok-free.app', 'https://*.ngrok.io']
SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')  # so the Callback URL shows https://

# Simple SQLite Database
DATABASES = {
    'default': env.db('DATABASE_URL', default=f"sqlite:///{BASE_DIR / 'db.sqlite3'}")
}
if DATABASES['default']['ENGINE'].endswith('sqlite3'):
    # The AI worker writes from several threads: wait for the lock instead of "database is locked",
    # let reads run during writes (WAL), and take the write lock up front (no upgrade deadlocks).
    DATABASES['default']['OPTIONS'] = {
        'timeout': 20,
        'transaction_mode': 'IMMEDIATE',
        'init_command': 'PRAGMA journal_mode=WAL; PRAGMA synchronous=NORMAL;',
    }
