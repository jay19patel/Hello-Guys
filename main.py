

import logging
import os
from datetime import datetime

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.templating import Jinja2Templates
from pymongo import DESCENDING, MongoClient
from pymongo.errors import PyMongoError

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ig-automation")

# ----------------------------------------------------------------------------
# MongoDB setup — events and logs are stored in separate collections so the
# dashboard can read persisted history instead of an in-memory list.
# ----------------------------------------------------------------------------
MONGODB_URI = os.getenv("MONGODB_URI")
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "whatsapp_sync")

if not MONGODB_URI:
    raise RuntimeError("MONGODB_URI is not set. Add it to your .env file.")

mongo_client = MongoClient(MONGODB_URI)
db = mongo_client[MONGODB_DB_NAME]
events_collection = db["events"]
logs_collection = db["logs"]


class MongoLogHandler(logging.Handler):
    def emit(self, record):
        try:
            logs_collection.insert_one({
                "level": record.levelname,
                "message": self.format(record),
                "time": datetime.now().strftime("%H:%M:%S"),
            })
        except PyMongoError:
            # Never let a logging failure crash the app
            pass


_log_handler = MongoLogHandler()
_log_handler.setFormatter(logging.Formatter("%(message)s"))
logging.getLogger().addHandler(_log_handler)

# ----------------------------------------------------------------------------
# Config (set these as environment variables, never hardcode in prod)
# ----------------------------------------------------------------------------
WEBHOOK_VERIFY_TOKEN = os.getenv("WEBHOOK_VERIFY_TOKEN", "change-this-verify-token")  # your own custom token — must match the "Verify Token" field you enter in Meta App Dashboard -> Webhooks

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
# 2. Main webhook receiver (POST) - just store + log, no processing
# ----------------------------------------------------------------------------
@app.post("/webhook")
async def receive_webhook(request: Request):
    payload = await request.json()

    events_collection.insert_one({
        "received_at": datetime.now().strftime("%H:%M:%S"),
        "raw": payload,
    })
    webhook_count = events_collection.count_documents({})
    logger.info(f"Incoming webhook #{webhook_count}: {payload}")

    return {"status": "received"}


@app.get("/health")
async def health():
    try:
        mongo_client.admin.command("ping")
        db_status = "connected"
    except PyMongoError:
        db_status = "disconnected"
    return {"status": "ok", "service": "instagram-webhook-monitor", "database": db_status}


# ----------------------------------------------------------------------------
# 3. Privacy Policy - required by Meta App Dashboard for webhook setup
# ----------------------------------------------------------------------------
@app.get("/privacy-policy")
async def privacy_policy(request: Request):
    return templates.TemplateResponse(request=request, name="privacy_policy.html")


# ----------------------------------------------------------------------------
# 4. Dashboard - track webhook count + raw events + logs
# ----------------------------------------------------------------------------
@app.get("/")
async def dashboard(request: Request):
    return templates.TemplateResponse(request=request, name="dashboard.html")


# -------------------------------
# Events API (polled every 10s)
# -------------------------------
@app.get("/events")
async def get_events():
    webhook_count = events_collection.count_documents({})
    cursor = events_collection.find({}, {"_id": 0}).sort("_id", DESCENDING).limit(20)
    return {
        "webhook_count": webhook_count,
        "events": list(cursor),
    }


# -------------------------------
# Logs API (polled every 10s)
# -------------------------------
@app.get("/logs")
async def get_logs():
    cursor = logs_collection.find({}, {"_id": 0}).sort("_id", DESCENDING).limit(50)
    return {
        "logs": list(cursor),
    }
