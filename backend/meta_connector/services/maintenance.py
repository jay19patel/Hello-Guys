import logging
import threading
from datetime import timedelta
from django.db import connection
from django.db.models import F, Q
from django.utils import timezone
from meta_connector.models import MetaConfig, WebhookLog

logger = logging.getLogger(__name__)
FAILED_LOG_MIN_DAYS = 90  # failed webhooks hold the only copy of a message we couldn't process; keep them longer
RUN_EVERY = timedelta(hours=24)


def cleanup_webhook_logs(config: MetaConfig = None, force: bool = False) -> int:
    """Deletes old raw webhook logs per Settings → retention. Cheap no-op unless 24h passed (or force). Returns rows deleted."""
    config = config or MetaConfig.get_solo()
    now = timezone.now()
    if not force and config.last_log_cleanup_at and now - config.last_log_cleanup_at < RUN_EVERY:
        return 0
    days = config.webhook_log_retention_days
    done, _ = WebhookLog.objects.filter(processed=True, created_at__lt=now - timedelta(days=days)).delete()
    failed, _ = WebhookLog.objects.filter(processed=False, created_at__lt=now - timedelta(days=max(days, FAILED_LOG_MIN_DAYS))).delete()
    config.last_log_cleanup_at = now
    config.save(update_fields=['last_log_cleanup_at'])
    if done or failed:
        logger.info("Webhook log cleanup: deleted %s processed and %s failed logs (retention %s days).", done, failed, days)
    return done + failed


# ---------- Webhook processing queue (background thread + retry) ----------
# shortcut: a daemon thread per webhook, not a durable queue (Celery/RQ). If the server stops mid-way the log stays
# unprocessed and shows up as "stuck" for retry. Upgrade to a real queue when traffic or uptime needs grow.
STUCK_AFTER = timedelta(minutes=2)
SIGNATURE_ALERT_WINDOW = timedelta(hours=24)


def process_in_background(webhook_log_id: int) -> None:
    from meta_connector.services.webhook_processor import process_webhook_payload

    def run():
        try:
            process_webhook_payload(webhook_log_id)
            cleanup_webhook_logs()
        except Exception:  # never let a thread die silently; the log keeps the error for retry
            logger.exception("Background processing of WebhookLog #%s crashed", webhook_log_id)
        finally:
            connection.close()  # threads get their own DB connection; close it
    threading.Thread(target=run, daemon=True, name=f"webhook-{webhook_log_id}").start()


def problem_webhooks():
    """Logs that failed, or were never processed (server stopped / thread crashed)."""
    return WebhookLog.objects.filter(processed=False).filter(~Q(error_message='') | Q(created_at__lt=timezone.now() - STUCK_AFTER))


def retry_webhooks(limit: int = 500):
    """Re-processes failed/stuck logs, oldest first. Safe to repeat: messages are de-duplicated by Meta ID."""
    from meta_connector.services.webhook_processor import process_webhook_payload
    ids = list(problem_webhooks().order_by('created_at').values_list('id', flat=True)[:limit])
    for log_id in ids:
        process_webhook_payload(log_id)
    still_failing = WebhookLog.objects.filter(id__in=ids, processed=False).count()
    return len(ids) - still_failing, still_failing


def record_signature_failure() -> None:
    MetaConfig.objects.filter(pk=MetaConfig.get_solo().pk).update(
        signature_failures=F('signature_failures') + 1, last_signature_failure_at=timezone.now())


def webhook_health() -> dict:
    """What the admin warning banner needs."""
    config = MetaConfig.get_solo()
    recent = config.last_signature_failure_at and timezone.now() - config.last_signature_failure_at < SIGNATURE_ALERT_WINDOW
    return {
        'failed_webhooks': problem_webhooks().count(),
        'signature_failures': config.signature_failures if recent else 0,
        'last_signature_failure_at': config.last_signature_failure_at,
    }
