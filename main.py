

import hashlib
import hmac
import logging
import os
from datetime import datetime
from typing import Optional

from dotenv import load_dotenv
from fastapi import FastAPI, Header, HTTPException, Request, Response
from fastapi.templating import Jinja2Templates

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ig-automation")

# In-memory store for the / dashboard (resets on server restart)
webhook_count: int = 0
recent_events: list[dict] = []
app_logs: list[dict] = []


class InMemoryLogHandler(logging.Handler):
    def emit(self, record):
        app_logs.append({
            "level": record.levelname,
            "message": self.format(record),
            "time": datetime.now().strftime("%H:%M:%S"),
        })
        del app_logs[:-200]  # keep only the latest 200


_log_handler = InMemoryLogHandler()
_log_handler.setFormatter(logging.Formatter("%(message)s"))
logging.getLogger().addHandler(_log_handler)

# ----------------------------------------------------------------------------
# Config (set these as environment variables, never hardcode in prod)
# ----------------------------------------------------------------------------
WEBHOOK_VERIFY_TOKEN = os.getenv("WEBHOOK_VERIFY_TOKEN", "change-this-verify-token")  # your own custom token — must match the "Verify Token" field you enter in Meta App Dashboard -> Webhooks
FACEBOOK_APP_SECRET = os.getenv("FACEBOOK_APP_SECRET", "")  # Facebook App Secret (App Dashboard -> App Settings -> Basic -> "App secret") — used to verify the X-Hub-Signature-256 header on incoming webhooks

app = FastAPI(title="Instagram Webhook Monitor")
templates = Jinja2Templates(directory="templates")


# ----------------------------------------------------------------------------
# 1. Webhook verification (GET) - Meta calls this once when you set webhook URL
# NOTE: Meta sends query params as hub.mode, hub.challenge, hub.verify_token
# (with dots) which aren't valid Python identifiers, so we read raw query params.
# ----------------------------------------------------------------------------
@app.get("/webhook")
async def verify_webhook(request: Request):
    params = request.query_params
    mode = params.get("hub.mode")
    token = params.get("hub.verify_token")
    challenge = params.get("hub.challenge")

    if mode == "subscribe" and token == WEBHOOK_VERIFY_TOKEN:
        logger.info("Webhook verified successfully")
        return Response(content=challenge, media_type="text/plain", status_code=200)

    raise HTTPException(status_code=403, detail="Verification failed")


# ----------------------------------------------------------------------------
# 2. Signature verification helper (validate that request really came from Meta)
# ----------------------------------------------------------------------------
def verify_signature(payload_body: bytes, signature_header: Optional[str]) -> bool:
    if not FACEBOOK_APP_SECRET:
        logger.warning("FACEBOOK_APP_SECRET not set — skipping signature check (dev only!)")
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected_sig = hmac.new(
        FACEBOOK_APP_SECRET.encode("utf-8"), payload_body, hashlib.sha256
    ).hexdigest()
    received_sig = signature_header.split("sha256=")[-1]
    return hmac.compare_digest(expected_sig, received_sig)


# ----------------------------------------------------------------------------
# 3. Main webhook receiver (POST) - just store + log, no processing
# ----------------------------------------------------------------------------
@app.post("/webhook")
async def receive_webhook(
    request: Request,
    x_hub_signature_256: Optional[str] = Header(default=None),
):
    global webhook_count

    raw_body = await request.body()

    if not verify_signature(raw_body, x_hub_signature_256):
        raise HTTPException(status_code=403, detail="Invalid signature")

    payload = await request.json()
    webhook_count += 1
    recent_events.append({
        "received_at": datetime.now().strftime("%H:%M:%S"),
        "raw": payload,
    })
    logger.info(f"Incoming webhook #{webhook_count}: {payload}")

    return {"status": "received"}


@app.get("/health")
async def health():
    return {"status": "ok", "service": "instagram-webhook-monitor"}


# ----------------------------------------------------------------------------
# 4. Privacy Policy - required by Meta App Dashboard for webhook setup
# ----------------------------------------------------------------------------
@app.get("/privacy-policy")
async def privacy_policy(request: Request):
    return templates.TemplateResponse(request=request, name="privacy_policy.html")


# ----------------------------------------------------------------------------
# 5. Dashboard - track webhook count + raw events + logs
# ----------------------------------------------------------------------------
@app.get("/")
async def dashboard(request: Request):
    return templates.TemplateResponse(request=request, name="dashboard.html")


# -------------------------------
# Events API (polled every 10s)
# -------------------------------
@app.get("/events")
async def get_events():
    return {
        "webhook_count": webhook_count,
        "events": recent_events[-20:][::-1],   # latest 20, newest first
    }


# -------------------------------
# Logs API (polled every 10s)
# -------------------------------
@app.get("/logs")
async def get_logs():
    return {
        "logs": app_logs[-50:][::-1],   # latest 50, newest first
    }
