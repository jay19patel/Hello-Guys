# Meta Connector - WhatsApp & Instagram Unified Backend (Simple SQLite Setup)

[![Python Version](https://img.shields.io/badge/python-3.12-blue.svg)](https://www.python.org/)
[![Django Version](https://img.shields.io/badge/django-5.1-brightgreen.svg)](https://www.djangoproject.com/)
[![REST Framework](https://img.shields.io/badge/DRF-3.15-red.svg)](https://www.django-rest-framework.org/)
[![Database](https://img.shields.io/badge/database-SQLite-lightgrey.svg)](https://www.sqlite.org/)

A lightweight, production-clean, single-app Django backend connecting **Meta WhatsApp Cloud API** and **Instagram Messaging API** into a single SQLite database. 
- **Zero Celery / Redis overhead**: Webhook payloads and messages are processed directly and reliably in SQLite.
- **Zero Docker / Nginx overhead**: Standard, clean local Django setup with simple environment configuration.
- **Folder-wise Modular Architecture**: Models, views, and services are organized cleanly into dedicated modules within the single `meta_connector` app.

---

## Folder-Wise Clean Project Structure

```
backend/
├── manage.py
├── requirements.txt
├── .env.example
├── .env
├── README.md
├── config/
│   ├── __init__.py
│   ├── urls.py
│   ├── wsgi.py
│   ├── asgi.py
│   └── settings/
│       ├── __init__.py
│       ├── base.py
│       ├── local.py
│       └── production.py
└── meta_connector/                      # SINGLE UNIFIED APP
    ├── __init__.py
    ├── apps.py
    ├── admin.py                         # Django Admin with Contact Notes Inline
    ├── serializers.py                   # DRF Serializers
    ├── urls.py                          # Unified URL Routing
    ├── utils.py                         # HMAC SHA-256 & Token Encryption
    │
    ├── models/                          # 📁 MODULAR MODELS FOLDER
    │   ├── __init__.py                  # Exports all models
    │   ├── account.py                   # PlatformAccount (WhatsApp & Instagram accounts)
    │   ├── contact.py                  # Contact
    │   ├── message.py                   # Message (Unified incoming & outgoing messages)
    │   └── webhook.py                   # WebhookLog (Raw audit log for debugging)
    │
    ├── views/                           # 📁 MODULAR VIEWS FOLDER
    │   ├── __init__.py                  # Exports all views
    │   ├── webhook_views.py             # meta_webhook_view (GET verify & POST ingest)
    │   ├── dashboard_views.py           # DashboardIndexView & DashboardOverviewAPIView
    │   └── api_views.py                 # ContactListAPIView, MessageListAPIView, SendMessageAPIView
    │
    ├── services/                        # 📁 BUSINESS SERVICES
    │   ├── __init__.py
    │   ├── whatsapp_service.py          # WhatsApp Cloud API client
    │   ├── instagram_service.py         # Instagram Messaging client
    │   ├── message_service.py           # Two-way outbound dispatcher
    │   └── webhook_processor.py         # Synchronous webhook parser & deduplicator
    │
    ├── templates/                       # 📁 FRONTEND TEMPLATES
    │   └── dashboard/
    │       └── index.html               # Responsive Tailwind CSS Dashboard UI
    │
    └── tests/                           # 📁 TEST SUITE
        ├── __init__.py
        ├── test_webhook.py              # Webhook GET challenge & deduplication tests
        ├── test_services.py             # WhatsApp/Instagram sending tests
        └── test_dashboard.py            # Dashboard UI rendering tests
```

---

## Active URL Routes

| URL Route | Method | Description |
|---|---|---|
| `http://127.0.0.1:8085/dashboard/` | GET | **Live Dashboard UI** (Metrics, Messages Stream, Contacts) |
| `http://127.0.0.1:8085/api/meta/webhook/` | GET / POST | **Meta Webhook** (GET verification & POST instant ingest) |
| `http://127.0.0.1:8085/api/meta/contacts/` | GET | List Contacts (with filtering & search) |
| `http://127.0.0.1:8085/api/meta/messages/` | GET | Paginated Messages (`?platform=whatsapp&contact_id=1`) |
| `http://127.0.0.1:8085/api/meta/send/` | POST | Send Outbound WhatsApp / Instagram message |
| `http://127.0.0.1:8085/admin/` | ALL | Django Admin Panel |

---

## Quick Start (Run on Port 8085)

```bash
cd backend
source ../.venv/bin/activate

# 1. Run migrations (SQLite)
python manage.py migrate

# 2. Run unit tests
python manage.py test

# 3. Start development server on port 8085
python manage.py runserver 8085
```

Open:
- Dashboard: [http://127.0.0.1:8085/dashboard/](http://127.0.0.1:8085/dashboard/)
- Admin Panel: [http://127.0.0.1:8085/admin/](http://127.0.0.1:8085/admin/)
