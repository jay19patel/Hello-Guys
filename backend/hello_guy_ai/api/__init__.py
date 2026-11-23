"""REST API (Django Ninja). Mounted at /api/ai/."""

from django.db import connection
from ninja import NinjaAPI
from ninja.responses import Status
from ninja.security import django_auth

from hello_guy_ai.api.internal import router as internal_router
from hello_guy_ai.api.public import router as public_router
from hello_guy_ai.api.schemas import HealthOut
from hello_guy_ai.core import events
from hello_guy_ai.exceptions import AssistantNotFound, NotAllowed, RunConflict, ThreadBusy
from hello_guy_ai.models import Run, Thread

api = NinjaAPI(
    title="Hello Guy AI",
    version="2.0.0",
    urls_namespace="hello_guy_ai",
    auth=django_auth,
)


@api.exception_handler(AssistantNotFound)
@api.exception_handler(Thread.DoesNotExist)
@api.exception_handler(Run.DoesNotExist)
def _not_found(request, exc):
    return api.create_response(request, {"message": "Not found"}, status=404)


@api.exception_handler(NotAllowed)
def _forbidden(request, exc):
    return api.create_response(request, {"message": str(exc)}, status=403)


@api.exception_handler(ThreadBusy)
@api.exception_handler(RunConflict)
def _conflict(request, exc):
    return api.create_response(request, {"message": str(exc)}, status=409)


@api.exception_handler(ValueError)
def _bad_request(request, exc):
    return api.create_response(request, {"message": str(exc)}, status=400)


@api.get("/health", auth=None, response={200: HealthOut, 503: HealthOut}, tags=["ops"])
def health(request):
    """Liveness for load balancers: database and Redis reachable."""
    status = {"database": _ok(lambda: connection.ensure_connection()), "redis": _ok(lambda: events.get_redis().ping())}
    return Status(200 if all(status.values()) else 503, status)


def _ok(probe) -> bool:
    try:
        probe()
    except Exception:
        return False
    return True


api.add_router("", internal_router)
api.add_router("/public", public_router)

