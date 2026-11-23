"""Dramatiq worker entrypoint. Run from your project root:

    DJANGO_SETTINGS_MODULE=myproject.settings dramatiq hello_guy_ai.tasks.worker

Dramatiq imports this module in each worker process, so Django is set up before the actors load.
"""

import os

import django

if not os.environ.get("DJANGO_SETTINGS_MODULE"):
    raise RuntimeError("Set DJANGO_SETTINGS_MODULE before starting the hello_guy_ai worker")
django.setup()

from hello_guy_ai.tasks import actors  # noqa: E402,F401  registers the actors
