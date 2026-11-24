import hmac
import json
import logging
from django.http import HttpResponse, HttpResponseForbidden, HttpResponseBadRequest
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from meta_connector.models import WebhookLog, PlatformAccount, MetaConfig
from meta_connector.utils import verify_meta_signature, sanitize_headers
from meta_connector.services.webhook_processor import process_webhook_payload
from meta_connector.services.maintenance import cleanup_webhook_logs, process_in_background, record_signature_failure
from django.conf import settings
from django.db import transaction

logger = logging.getLogger(__name__)


@csrf_exempt
@require_http_methods(["GET", "POST"])
def meta_webhook_view(request):
    """
    Unified Meta Webhook endpoint for WhatsApp Cloud API & Instagram Messaging API.
    GET: Handles Meta hub subscription verification challenge.
    POST: Synchronously logs incoming raw event and processes message data cleanly in SQLite.
    """

    # --- 1. GET: Webhook Subscription Verification ---
    config = MetaConfig.get_solo()
    if request.method == "GET":
        hub_mode = request.GET.get('hub.mode')
        hub_verify_token = request.GET.get('hub.verify_token')
        hub_challenge = request.GET.get('hub.challenge')

        if hub_mode == 'subscribe' and hub_verify_token:
            expected_token = config.verify_token
            token_matches = bool(expected_token) and hmac.compare_digest(hub_verify_token, expected_token)

            if not token_matches:
                token_matches = PlatformAccount.objects.filter(
                    verify_token=hub_verify_token,
                    is_active=True
                ).exists()

            if token_matches and hub_challenge:
                logger.info("Meta webhook verification challenge succeeded.")
                return HttpResponse(hub_challenge, content_type="text/plain", status=200)

        logger.warning("Meta webhook verification failed: hub_mode=%s", hub_mode)  # never log the submitted token
        return HttpResponseForbidden("Verification token mismatch", content_type="text/plain")

    # --- 2. POST: Process Incoming Webhook Event ---
    app_secret = config.app_secret
    if not app_secret and settings.META_REQUIRE_SIGNATURE:
        # Without the App Secret nobody can tell Meta's events from forged ones (which would trigger real replies).
        logger.error("Rejected webhook POST: no App Secret configured (Dashboard > Settings).")
        return HttpResponse("Webhook not configured", content_type="text/plain", status=503)
    if app_secret:
        signature = request.headers.get('X-Hub-Signature-256')
        if not verify_meta_signature(request.body, signature, app_secret):
            logger.warning("Rejected webhook POST with invalid X-Hub-Signature-256.")
            record_signature_failure()  # surfaces on the dashboard: usually a wrong App Secret
            return HttpResponseForbidden("Invalid signature", content_type="text/plain")

    try:
        payload = json.loads(request.body.decode('utf-8')) if request.body else {}
    except (ValueError, UnicodeDecodeError) as exc:
        logger.error("Failed to parse JSON body from webhook: %s", exc)
        return HttpResponseBadRequest("Invalid JSON body", content_type="text/plain")
    if not isinstance(payload, dict):
        return HttpResponseBadRequest("Expected a JSON object", content_type="text/plain")

    meta_obj = payload.get('object', '')
    if 'whatsapp' in meta_obj:
        platform = PlatformAccount.PLATFORM_WHATSAPP
    elif meta_obj in ['instagram', 'page']:
        platform = PlatformAccount.PLATFORM_INSTAGRAM
    else:
        platform = meta_obj or 'unknown'

    webhook_log = WebhookLog.objects.create(
        platform=platform,
        payload=payload,
        headers=sanitize_headers(dict(request.headers)),
        processed=False
    )

    # Process payload synchronously directly in SQLite (no Celery needed)
    if settings.META_WEBHOOK_ASYNC:
        # Answer Meta right away; slow work (auto-DM, profile lookups) happens after the response.
        transaction.on_commit(lambda: process_in_background(webhook_log.id))
    else:
        process_webhook_payload(webhook_log.id)
        cleanup_webhook_logs(config)  # at most once a day; keeps the log table from growing forever

    return HttpResponse("EVENT_RECEIVED", content_type="text/plain", status=200)
