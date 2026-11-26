"""URL guards shared by the reminder/notification code and views."""

from urllib.parse import urlparse

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


def redirect_next(request, default: str):
    """Redirect to the `next` param only when it points back at this site, else to `default`."""
    target = request.POST.get("next") or request.GET.get("next")
    if target and url_has_allowed_host_and_scheme(
        target, allowed_hosts={request.get_host()}, require_https=request.is_secure()
    ):
        return redirect(target)
    return redirect(default)
