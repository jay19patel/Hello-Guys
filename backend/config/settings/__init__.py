import os

# Automatically load local or production settings based on DJANGO_ENV
django_env = os.environ.get('DJANGO_ENV', 'local').strip().lower()

if django_env == 'production':
    from .production import *
else:
    from .local import *
