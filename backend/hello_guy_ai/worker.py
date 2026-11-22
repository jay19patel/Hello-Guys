"""Dramatiq worker entrypoint. Run from backend/:  dramatiq hello_guy_ai.worker

Dramatiq imports this module in each worker process, so Django is set up before the actors load.
"""

import os

import django

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings")
django.setup()

from hello_guy_ai import tasks  # noqa: E402,F401  registers the actors
