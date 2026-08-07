from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
import uvicorn
import logging
from datetime import datetime

app = FastAPI(title="WhatsApp Webhook")

logging.basicConfig(level=logging.INFO)

VERIFY_TOKEN = "5eef6a56-e72b-477c-87f8-70484ecbb750"

# In-memory store
webhook_count: int = 0
recent_messages: list[dict] = []
seen_message_ids: set[str] = set()  # dedup for Meta's retried deliveries (up to 36h)
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


# -------------------------------
# Dashboard (HTML)
# -------------------------------
@app.get("/", response_class=HTMLResponse)
async def dashboard():
    html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0"/>
  <title>WhatsApp Webhook</title>
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
      background: #e8f5e9;
      color: #2e7d32;
      font-size: 0.72rem;
      font-weight: 600;
      padding: 2px 10px;
      border-radius: 20px;
      margin-bottom: 16px;
    }
    .meta { font-size: 0.85rem; color: #666; }
    .stat { font-size: 2.4rem; font-weight: 700; color: #25D366; margin: 8px 0 4px; }
    .label { font-size: 0.8rem; color: #888; text-transform: uppercase; letter-spacing: .05em; }
    #msg-list { margin-top: 16px; }
    .msg-item {
      border-left: 3px solid #25D366;
      padding: 8px 12px;
      margin-bottom: 10px;
      background: #f9f9f9;
      border-radius: 0 6px 6px 0;
      font-size: 0.85rem;
    }
    .msg-item .from { font-weight: 600; }
    .msg-item .time { font-size: 0.75rem; color: #aaa; float: right; }
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
      background: #25D366;
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
      background: #25D366;
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
    <h1>WhatsApp Webhook <span id="refresh-dot"></span></h1>
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
      <div class="label">Recent Messages <span id="msg-count" style="color:#25D366;font-weight:700;"></span></div>
      <div style="font-size:0.78rem; color:#aaa;">Next refresh in <span id="countdown" style="color:#25D366;font-weight:600;">10</span>s</div>
    </div>
    <div id="msg-list"><p class="empty">Loading…</p></div>
  </div>

  <div class="card">
    <div class="label">Server Logs <span id="log-count" style="color:#25D366;font-weight:700;"></span></div>
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
        const res = await fetch('/messages');
        const data = await res.json();
        document.getElementById('count').textContent = data.webhook_count;

        const msgCount = data.messages.length;
        document.getElementById('msg-count').textContent = msgCount ? `(${msgCount})` : '';

        const list = document.getElementById('msg-list');
        if (!msgCount) {
          list.innerHTML = '<p class="empty">No messages yet.</p>';
        } else {
          list.innerHTML = data.messages.map(m => `
            <div class="msg-item">
              <span class="from">${m.sender || 'unknown'}</span>
              <span class="time">${m.received_at}</span><br/>
              <span>${m.text || '[' + m.type + ']'}</span>
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
# Messages API (polled every 10s)
# -------------------------------
@app.get("/messages")
async def get_messages():
    return {
        "webhook_count": webhook_count,
        "messages": recent_messages[-20:][::-1],   # latest 20, newest first
    }


# -------------------------------
# Logs API (polled every 10s)
# -------------------------------
@app.get("/logs")
async def get_logs():
    return {
        "logs": app_logs[-50:][::-1],   # latest 50, newest first
    }


# -------------------------------
# Health Check
# -------------------------------
@app.get("/health")
async def health():
    return {"status": "success", "message": "Webhook is running"}


# -------------------------------
# Webhook Verification (GET)
# -------------------------------
@app.get("/webhook")
async def verify_webhook(request: Request):
    mode = request.query_params.get("hub.mode")
    token = request.query_params.get("hub.verify_token")
    challenge = request.query_params.get("hub.challenge")

    if mode == "subscribe" and token == VERIFY_TOKEN:
        return PlainTextResponse(challenge)

    return PlainTextResponse("Verification failed", status_code=403)


# -------------------------------
# Receive WhatsApp Messages (POST)
# -------------------------------
@app.post("/webhook")
async def receive_webhook(request: Request):
    global webhook_count

    payload: dict = await request.json()
    webhook_count += 1

    object_type: str = payload.get("object", "unknown")
    logging.info("Webhook received | object=%s count=%d", object_type, webhook_count)

    try:
        for entry in payload.get("entry", []):
            for change in entry.get("changes", []):
                field = change.get("field", "")
                value = change.get("value", {})

                # ── Incoming Messages ──────────────────────────
                for message in value.get("messages", []):
                    sender       = message.get("from")
                    message_id   = message.get("id")
                    message_type = message.get("type")

                    if message_id in seen_message_ids:
                        logging.info("Duplicate message %s — skipped (retry)", message_id)
                        continue
                    seen_message_ids.add(message_id)

                    text = None
                    if message_type == "text":
                        text = message.get("text", {}).get("body")

                    recent_messages.append({
                        "event":       "message",
                        "sender":      sender,
                        "message_id":  message_id,
                        "type":        message_type,
                        "text":        text,
                        "field":       field,
                        "object":      object_type,
                        "received_at": datetime.now().strftime("%H:%M:%S"),
                    })
                    logging.info("📩 Message | From: %s | Type: %s | Text: %s", sender, message_type, text)

                # ── Status Updates (sent/delivered/read) ────────
                for status in value.get("statuses", []):
                    recipient = status.get("recipient_id")
                    st        = status.get("status")         # sent / delivered / read

                    status_key = f"{status.get('id')}:{st}"
                    if status_key in seen_message_ids:
                        logging.info("Duplicate status %s — skipped (retry)", status_key)
                        continue
                    seen_message_ids.add(status_key)

                    recent_messages.append({
                        "event":       "status",
                        "sender":      recipient,
                        "message_id":  status.get("id"),
                        "type":        f"status:{st}",
                        "text":        f"✔ {st}",
                        "field":       field,
                        "object":      object_type,
                        "received_at": datetime.now().strftime("%H:%M:%S"),
                    })
                    logging.info("📬 Status | Recipient: %s | Status: %s", recipient, st)

    except Exception as e:
        logging.error("Error parsing webhook: %s", e)

    return {"success": True}


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)