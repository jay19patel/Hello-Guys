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
from datetime import datetime
from typing import Optional

import httpx
from dotenv import load_dotenv
from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ig-automation")

# In-memory store for the /  dashboard (resets on server restart)
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


@app.get("/health")
async def health():
    return {"status": "ok", "service": "instagram-automation"}


# ----------------------------------------------------------------------------
# 7. Dashboard (HTML) - track webhook count + raw events + logs
# ----------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
async def dashboard():
    html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>Instagram Automation</title>
  <style>
    * { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: 'Segoe UI', sans-serif;
      background: #f5f5f5;
      color: #222;
      padding: 40px 20px;
    }
    .card {
      background: #fff;
      border-radius: 12px;
      padding: 28px 32px;
      max-width: 640px;
      margin: 0 auto 24px;
      box-shadow: 0 2px 8px rgba(0,0,0,0.08);
    }
    h1 { font-size: 1.4rem; font-weight: 700; margin-bottom: 4px; }
    .badge {
      display: inline-block;
      background: #fce4ec;
      color: #c2185b;
      font-size: 0.72rem;
      font-weight: 600;
      padding: 2px 10px;
      border-radius: 20px;
      margin-bottom: 16px;
    }
    .meta { font-size: 0.85rem; color: #666; }
    .stat { font-size: 2.4rem; font-weight: 700; color: #E1306C; margin: 8px 0 4px; }
    .label { font-size: 0.8rem; color: #888; text-transform: uppercase; letter-spacing: .05em; }
    #event-list { margin-top: 16px; }
    .event-item {
      border-left: 3px solid #E1306C;
      padding: 8px 12px;
      margin-bottom: 10px;
      background: #f9f9f9;
      border-radius: 0 6px 6px 0;
      font-size: 0.85rem;
    }
    .event-item .time { font-size: 0.75rem; color: #aaa; float: right; }
    .event-item pre {
      white-space: pre-wrap;
      word-break: break-word;
      margin-top: 4px;
      font-family: 'SF Mono', Consolas, monospace;
      font-size: 0.78rem;
    }
    .empty { color: #aaa; font-size: 0.85rem; }
    .url-row {
      display: flex;
      align-items: center;
      gap: 8px;
      margin-top: 8px;
      background: #f5f5f5;
      border-radius: 8px;
      padding: 10px 12px;
    }
    .url-row input {
      flex: 1;
      border: none;
      background: transparent;
      font-family: 'SF Mono', Consolas, monospace;
      font-size: 0.85rem;
      color: #222;
      outline: none;
    }
    .copy-btn {
      border: none;
      background: #E1306C;
      color: #fff;
      font-size: 0.78rem;
      font-weight: 600;
      padding: 6px 14px;
      border-radius: 6px;
      cursor: pointer;
      white-space: nowrap;
    }
    .copy-btn:active { transform: scale(0.97); }
    #log-list {
      margin-top: 16px;
      max-height: 320px;
      overflow-y: auto;
      background: #10151a;
      border-radius: 8px;
      padding: 12px;
    }
    .log-item {
      font-family: 'SF Mono', Consolas, monospace;
      font-size: 0.78rem;
      color: #cfd8dc;
      padding: 3px 0;
      white-space: pre-wrap;
      word-break: break-word;
    }
    .log-item .time { color: #6c7a89; margin-right: 8px; }
    .log-item.level-WARNING { color: #ffca28; }
    .log-item.level-ERROR { color: #ef5350; }
    #refresh-dot {
      display: inline-block;
      width: 8px; height: 8px;
      border-radius: 50%;
      background: #E1306C;
      margin-left: 6px;
      animation: pulse 2s infinite;
    }
    @keyframes pulse {
      0%,100% { opacity: 1; } 50% { opacity: 0.3; }
    }
  </style>
</head>
<body>

  <div class="card">
    <h1>Instagram Automation <span id="refresh-dot"></span></h1>
    <span class="badge">v1.0.0</span>
    <p class="meta">Live dashboard &mdash; auto-refreshes every 10 seconds.</p>
  </div>

  <div class="card">
    <div class="label">Webhook URL</div>
    <div class="url-row">
      <input type="text" id="webhook-url" readonly value="Loading…" />
      <button class="copy-btn" id="copy-btn" onclick="copyWebhookUrl()">Copy</button>
    </div>
  </div>

  <div class="card">
    <div class="label">Total Webhooks Received</div>
    <div class="stat" id="count">—</div>
    <div class="label">since server start</div>
  </div>

  <div class="card">
    <div style="display:flex; align-items:center; justify-content:space-between; margin-bottom:12px;">
      <div class="label">Recent Events <span id="event-count" style="color:#E1306C;font-weight:700;"></span></div>
      <div style="font-size:0.78rem; color:#aaa;">Next refresh in <span id="countdown" style="color:#E1306C;font-weight:600;">10</span>s</div>
    </div>
    <div id="event-list"><p class="empty">Loading…</p></div>
  </div>

  <div class="card">
    <div class="label">Server Logs <span id="log-count" style="color:#E1306C;font-weight:700;"></span></div>
    <div id="log-list"><p class="empty">Loading…</p></div>
  </div>

  <script>
    let secondsLeft = 10;

    document.getElementById('webhook-url').value = window.location.origin + '/webhook';

    function copyWebhookUrl() {
      const input = document.getElementById('webhook-url');
      navigator.clipboard.writeText(input.value).then(() => {
        const btn = document.getElementById('copy-btn');
        const original = btn.textContent;
        btn.textContent = 'Copied!';
        setTimeout(() => { btn.textContent = original; }, 1500);
      });
    }

    async function refresh() {
      try {
        const res = await fetch('/events');
        const data = await res.json();
        document.getElementById('count').textContent = data.webhook_count;

        const eventCount = data.events.length;
        document.getElementById('event-count').textContent = eventCount ? `(${eventCount})` : '';

        const list = document.getElementById('event-list');
        if (!eventCount) {
          list.innerHTML = '<p class="empty">No events yet.</p>';
        } else {
          list.innerHTML = data.events.map(e => `
            <div class="event-item">
              <span class="time">${e.received_at}</span>
              <pre>${JSON.stringify(e.raw, null, 2)}</pre>
            </div>
          `).join('');
        }
      } catch(e) {
        console.error(e);
      }

      try {
        const logRes = await fetch('/logs');
        const logData = await logRes.json();

        const logCount = logData.logs.length;
        document.getElementById('log-count').textContent = logCount ? `(${logCount})` : '';

        const logList = document.getElementById('log-list');
        if (!logCount) {
          logList.innerHTML = '<p class="empty">No logs yet.</p>';
        } else {
          logList.innerHTML = logData.logs.map(l => `
            <div class="log-item level-${l.level}">
              <span class="time">${l.time}</span>${l.message}
            </div>
          `).join('');
        }
      } catch(e) {
        console.error(e);
      }

      secondsLeft = 10;
    }

    // countdown tick every second
    setInterval(() => {
      secondsLeft = Math.max(0, secondsLeft - 1);
      document.getElementById('countdown').textContent = secondsLeft;
    }, 1000);

    refresh();
    setInterval(refresh, 10000);
  </script>
</body>
</html>"""
    return HTMLResponse(content=html)


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