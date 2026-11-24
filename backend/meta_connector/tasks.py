"""Durable webhook processing on the Dramatiq + Redis queue that hello_guy_ai already runs.
Used by services.maintenance.process_in_background when hello_guy_ai is installed."""

import dramatiq

from hello_guy_ai.tasks.actors import RUN_LIMIT_MS  # noqa: F401  importing it installs the Redis broker first
from meta_connector.services.maintenance import cleanup_webhook_logs
from meta_connector.services.webhook_processor import process_webhook_payload

WEBHOOK_QUEUE = "meta_webhooks"


# Retries only when the worker itself crashes mid-way: processing is idempotent (messages dedupe by Meta id)
# and records its own errors on the WebhookLog (retry from the dashboard).
@dramatiq.actor(queue_name=WEBHOOK_QUEUE, max_retries=3, min_backoff=10_000, time_limit=5 * 60 * 1000)
def process_webhook(webhook_log_id: int) -> None:
    process_webhook_payload(webhook_log_id)
    cleanup_webhook_logs()
