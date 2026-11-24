"""Picks settings by DJANGO_ENV: "production" or "local".

wsgi.py / asgi.py default to production, so a server started without DJANGO_ENV never runs with DEBUG on.
manage.py and the worker default to local; set DJANGO_ENV=production for them on a server.
"""

import os

django_env = os.environ.get('DJANGO_ENV', 'local').strip().lower()

if django_env == 'production':
    from .production import *
elif django_env == 'local':
    from .local import *
else:
    raise RuntimeError(f"DJANGO_ENV must be 'production' or 'local', not {django_env!r}")
