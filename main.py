

import logging
import os
from datetime import datetime

from bson import ObjectId
from bson.errors import InvalidId
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.templating import Jinja2Templates
from pymongo import DESCENDING, MongoClient, ReturnDocument
from pymongo.errors import PyMongoError

from services.base import WebhookService
from services.instagram import InstagramService

# WhatsAppService import intentionally left out — see ACTIVE_SERVICES below for
# how to bring WhatsApp back online.

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("ig-automation")

# ----------------------------------------------------------------------------
# MongoDB setup — logs are the single source of truth for the dashboard (every
# incoming webhook is already logged with its raw payload, so there's no
# separate "events" collection duplicating that data). The webhook counter is
# a single upserted document, incremented atomically, instead of a full
# collection scan/count on every request.
# ----------------------------------------------------------------------------
MONGODB_URI = os.getenv("MONGODB_URI")
MONGODB_DB_NAME = os.getenv("MONGODB_DB_NAME", "whatsapp_sync")

if not MONGODB_URI:
    raise RuntimeError("MONGODB_URI is not set. Add it to your .env file.")

mongo_client = MongoClient(MONGODB_URI)
db = mongo_client[MONGODB_DB_NAME]
logs_collection = db["logs"]
stats_collection = db["stats"]


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
GRAPH_API_VERSION = os.getenv("GRAPH_API_VERSION", "v26.0")

IG_ACCESS_TOKEN = os.getenv("IG_ACCESS_TOKEN")

app = FastAPI(title="Instagram + WhatsApp Webhook Monitor")
templates = Jinja2Templates(directory="templates")
instagram_service = InstagramService(access_token=IG_ACCESS_TOKEN, api_version=GRAPH_API_VERSION)

# Every service here gets matched against payload["object"] via its `object_type`
# and dispatched to automatically — no per-platform branching needed.
# To bring WhatsApp back: uncomment the import above, instantiate WhatsAppService
# (reading WHATSAPP_ACCESS_TOKEN / WHATSAPP_PHONE_NUMBER_ID from .env), and add
# it to this list.
ACTIVE_SERVICES: list[WebhookService] = [instagram_service]


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
# 2. Main webhook receiver (POST) - log, then dispatch to the right service.
# ----------------------------------------------------------------------------
@app.post("/webhook")
async def receive_webhook(request: Request):
    payload = await request.json()

    counter = stats_collection.find_one_and_update(
        {"_id": "webhook_count"},
        {"$inc": {"count": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )
    webhook_count = counter["count"]
    logger.info(f"Incoming webhook #{webhook_count} ({payload.get('object')}): {payload}")

    # Meta sends "object": "instagram" for IG events, "whatsapp_business_account"
    # for WhatsApp Cloud API events, etc. — match it against each active
    # service's object_type and dispatch to whichever one applies.
    for service in ACTIVE_SERVICES:
        if service.object_type == payload.get("object"):
            await service.handle_webhook(payload)
            break

    return {"status": "received"}


@app.get("/health")
async def health():
    try:
        mongo_client.admin.command("ping")
        db_status = "connected"
    except PyMongoError:
        db_status = "disconnected"
    return {"status": "ok", "service": "instagram-whatsapp-webhook-monitor", "database": db_status}


# ----------------------------------------------------------------------------
# 3. Privacy Policy - required by Meta App Dashboard for webhook setup
# ----------------------------------------------------------------------------
@app.get("/privacy-policy")
async def privacy_policy(request: Request):
    return templates.TemplateResponse(request=request, name="privacy_policy.html")


# ----------------------------------------------------------------------------
# 4. Dashboard - track webhook count + logs
# ----------------------------------------------------------------------------
@app.get("/")
async def dashboard(request: Request):
    return templates.TemplateResponse(request=request, name="dashboard.html")


# -------------------------------
# Stats API (polled every 10s) — single point lookup, no collection scan
# -------------------------------
@app.get("/stats")
async def get_stats():
    counter = stats_collection.find_one({"_id": "webhook_count"})
    return {"webhook_count": counter["count"] if counter else 0}


# -------------------------------
# Logs API — cursor/keyset pagination via before_id, so paging through old
# logs never needs an expensive `skip()` over the collection.
# -------------------------------
@app.get("/logs")
async def get_logs(before_id: str | None = None, limit: int = 20):
    limit = max(1, min(limit, 100))
    query = {}
    if before_id:
        try:
            query["_id"] = {"$lt": ObjectId(before_id)}
        except InvalidId:
            raise HTTPException(status_code=400, detail="Invalid before_id")

    docs = list(logs_collection.find(query).sort("_id", DESCENDING).limit(limit + 1))
    has_more = len(docs) > limit
    docs = docs[:limit]

    return {
        "logs": [
            {"id": str(d["_id"]), "level": d["level"], "message": d["message"], "time": d["time"]}
            for d in docs
        ],
        "has_more": has_more,
    }
