"""Shared URL/input guards and CBV mixins used across modules."""

import json
from typing import Any
from urllib.parse import urlparse

from django.http import HttpResponseBadRequest
from django.shortcuts import redirect
from django.utils.http import url_has_allowed_host_and_scheme


def safe_url(url, default: str = "") -> str:
    """Keep only a same-site relative path ('/x', not '//x') or an http(s) URL; anything else becomes `default`."""
    url = (url or "").strip()
    if not url or any(ord(c) < 33 or c == "\\" for c in url):
        return default
    if url.startswith("/"):
        return default if url.startswith("//") else url
    parsed = urlparse(url)
    return url if parsed.scheme in ("http", "https") and parsed.netloc else default


def safe_next(request, fallback: str) -> str:
    """Return the `next` param when it points back at this site, else `fallback`."""
    target = request.POST.get("next") or request.GET.get("next") or ""
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return target
    return fallback


def redirect_next(request, default: str):
    """Redirect to the validated `next` param if safe, else `default`."""
    return redirect(safe_next(request, default))


def int_or(value: Any, default: int = 0) -> int:
    """Parse `value` as int, returning `default` on missing or non-numeric input."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def json_body(request) -> dict:
    """Parse a JSON object body safely, returning `{}` on invalid or non-dict payloads."""
    try:
        data = json.loads(request.body.decode("utf-8") or "{}")
    except (ValueError, UnicodeDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


class ActionsMixin:
    """Dispatch `POST action=<name>` to `post_<name>()` (hyphens become underscores)."""

    action_exceptions: tuple[type[Exception], ...] = ()

    def post(self, request, *args, **kwargs):
        if hasattr(self, "get_object"):
            self.object = self.get_object()
        handler = getattr(self, f"post_{request.POST.get('action', '').replace('-', '_')}", None)
        if handler is None:
            return HttpResponseBadRequest("Unknown action")
        if not self.action_exceptions:
            return handler()
        try:
            return handler()
        except self.action_exceptions as exc:
            return self.handle_action_error(request, exc)

    def handle_action_error(self, request, exc: Exception):
        raise exc

    def rerender(self, **forms):
        """Re-render the detail page with invalid form errors preserved."""
        return self.render_to_response(self.get_context_data(**forms))
