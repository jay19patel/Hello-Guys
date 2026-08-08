"""
Instagram Automation Server (FastAPI)
--------------------------------------
Handles two things via Instagram Graph API webhooks:
1. Auto-COMMENT reply -> jab koi tumhare post/reel pe comment kare
2. Auto-DM reply       -> jab koi tumhe Instagram DM kare

Flow:
Meta -> sends webhook event (comment / message) -> FastAPI endpoint
     -> we check keyword/trigger rules
     -> we call Graph API to reply back automatically

Requirements (Meta side, one-time setup):
- Facebook App with "Instagram Graph API" + "Webhooks" product added
- Instagram Professional (Business/Creator) account linked to a Facebook Page
- Page Access Token with these permissions:
    instagram_basic, instagram_manage_comments, instagram_manage_messages,
    pages_show_list, pages_manage_metadata, pages_read_engagement
- Webhook subscribed to fields: comments, messages (for the Page/IG object)
"""

import hashlib
import hmac
import logging
import os
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, Response
from pydantic import BaseModel

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ig-automation")

# ----------------------------------------------------------------------------
# Config (set these as environment variables, never hardcode in prod)
# ----------------------------------------------------------------------------
VERIFY_TOKEN = os.getenv("IG_VERIFY_TOKEN", "change-this-verify-token")
APP_SECRET = os.getenv("IG_APP_SECRET", "")           # Facebook App secret (for signature check)
PAGE_ACCESS_TOKEN = os.getenv("IG_PAGE_ACCESS_TOKEN", "")  # Long-lived Page token
IG_BUSINESS_ACCOUNT_ID = os.getenv("IG_BUSINESS_ACCOUNT_ID", "")  # your IG account id

GRAPH_API_VERSION = "v26.0"
GRAPH_BASE_URL = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

# Simple keyword -> reply rules (customize / move to DB later)
COMMENT_AUTO_REPLIES = {
    "price": "Price details DM me bhej diye hai! Check karo 📩",
    "link": "Link bio me hai 🔗",
    "default": "Thanks for the comment! ❤️",
}

DM_AUTO_REPLIES = {
    "hi": "Hey! Welcome to NJTechStudio 👋 Kaise help kar sakta hu?",
    "price": "Pricing details ke liye thoda wait karo, team jaldi reply karegi.",
    "default": "Thanks for messaging! Hum jald hi reply karenge 🙌",
}

app = FastAPI(title="Instagram Automation Bot")


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

    if mode == "subscribe" and token == VERIFY_TOKEN:
        logger.info("Webhook verified successfully")
        return Response(content=challenge, media_type="text/plain", status_code=200)

    raise HTTPException(status_code=403, detail="Verification failed")


# ----------------------------------------------------------------------------
# 2. Signature verification helper (validate that request really came from Meta)
# ----------------------------------------------------------------------------
def verify_signature(payload_body: bytes, signature_header: Optional[str]) -> bool:
    if not APP_SECRET:
        logger.warning("APP_SECRET not set — skipping signature check (dev only!)")
        return True
    if not signature_header or not signature_header.startswith("sha256="):
        return False

    expected_sig = hmac.new(
        APP_SECRET.encode("utf-8"), payload_body, hashlib.sha256
    ).hexdigest()
    received_sig = signature_header.split("sha256=")[-1]
    return hmac.compare_digest(expected_sig, received_sig)


# ----------------------------------------------------------------------------
# 3. Main webhook receiver (POST) - all events (comments + messages) land here
# ----------------------------------------------------------------------------
@app.post("/webhook")
async def receive_webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_hub_signature_256: Optional[str] = Header(default=None),
):
    raw_body = await request.body()

    if not verify_signature(raw_body, x_hub_signature_256):
        raise HTTPException(status_code=403, detail="Invalid signature")

    payload = await request.json()
    logger.info(f"Incoming webhook: {payload}")

    for entry in payload.get("entry", []):
        # --- Comment events ---
        for change in entry.get("changes", []):
            if change.get("field") == "comments":
                background_tasks.add_task(handle_comment_event, change.get("value", {}))

        # --- DM / messaging events ---
        for messaging_event in entry.get("messaging", []):
            background_tasks.add_task(handle_message_event, messaging_event)

    # Always respond fast with 200, actual work happens in background
    return {"status": "received"}


# ----------------------------------------------------------------------------
# 4. Comment handler -> auto reply on the comment itself
# ----------------------------------------------------------------------------
async def handle_comment_event(value: dict):
    comment_id = value.get("id")
    comment_text = (value.get("text") or "").lower()
    from_user = value.get("from", {}).get("username", "unknown")

    if not comment_id:
        return

    # Don't reply to our own account's comments (avoid loops)
    if str(value.get("from", {}).get("id")) == IG_BUSINESS_ACCOUNT_ID:
        return

    reply_text = COMMENT_AUTO_REPLIES["default"]
    for keyword, reply in COMMENT_AUTO_REPLIES.items():
        if keyword != "default" and keyword in comment_text:
            reply_text = reply
            break

    logger.info(f"Replying to comment from @{from_user}: {comment_text!r} -> {reply_text!r}")
    await reply_to_comment(comment_id, reply_text)


async def reply_to_comment(comment_id: str, message: str):
    url = f"{GRAPH_BASE_URL}/{comment_id}/replies"
    params = {"message": message, "access_token": PAGE_ACCESS_TOKEN}

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, params=params)
        if resp.status_code != 200:
            logger.error(f"Comment reply failed: {resp.status_code} {resp.text}")
        else:
            logger.info(f"Comment reply sent: {resp.json()}")


# ----------------------------------------------------------------------------
# 5. DM handler -> auto reply via Instagram Messaging API
# ----------------------------------------------------------------------------
async def handle_message_event(event: dict):
    sender_id = event.get("sender", {}).get("id")
    message = event.get("message", {})
    text = (message.get("text") or "").lower()

    # Ignore echo events (messages sent BY our own account)
    if message.get("is_echo") or not sender_id:
        return

    reply_text = DM_AUTO_REPLIES["default"]
    for keyword, reply in DM_AUTO_REPLIES.items():
        if keyword != "default" and keyword in text:
            reply_text = reply
            break

    logger.info(f"Replying to DM from {sender_id}: {text!r} -> {reply_text!r}")
    await send_dm(sender_id, reply_text)


async def send_dm(recipient_id: str, message: str):
    url = f"{GRAPH_BASE_URL}/me/messages"
    payload = {
        "recipient": {"id": recipient_id},
        "message": {"text": message},
    }
    params = {"access_token": PAGE_ACCESS_TOKEN}

    async with httpx.AsyncClient() as client:
        resp = await client.post(url, params=params, json=payload)
        if resp.status_code != 200:
            logger.error(f"DM send failed: {resp.status_code} {resp.text}")
        else:
            logger.info(f"DM sent: {resp.json()}")


# ----------------------------------------------------------------------------
# 6. Manual test endpoints (optional, useful during dev)
# ----------------------------------------------------------------------------
class ManualReply(BaseModel):
    target_id: str
    message: str


@app.post("/test/comment-reply")
async def test_comment_reply(body: ManualReply):
    await reply_to_comment(body.target_id, body.message)
    return {"status": "sent"}


@app.post("/test/dm-reply")
async def test_dm_reply(body: ManualReply):
    await send_dm(body.target_id, body.message)
    return {"status": "sent"}


@app.get("/")
async def health():
    return {"status": "ok", "service": "instagram-automation"}