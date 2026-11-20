"""
Dashboard roles, stored as Django groups:
  Viewer - can look at everything except Settings; changes nothing.
  Agent  - Viewer + reply to contacts and set auto-DM messages. Cannot delete anything or open Settings.
  Admin  - everything: Settings, accounts, tokens, team, deleting, Django Admin.
Superusers are always Admin. A user with no role cannot open the dashboard.
"""
from functools import wraps
from django.contrib.auth.views import redirect_to_login
from django.core.exceptions import PermissionDenied
from rest_framework.permissions import BasePermission

ADMIN, AGENT, VIEWER = 'Admin', 'Agent', 'Viewer'
ROLES = (ADMIN, AGENT, VIEWER)
LEVEL = {VIEWER: 1, AGENT: 2, ADMIN: 3}


def get_role(user):
    if not user.is_authenticated or not user.is_active:
        return None
    if user.is_superuser:
        return ADMIN
    if not hasattr(user, '_dashboard_role'):  # one query per request
        names = set(user.groups.values_list('name', flat=True))
        user._dashboard_role = next((r for r in ROLES if r in names), None)
    return user._dashboard_role


def has_role(user, minimum: str) -> bool:
    role = get_role(user)
    return role is not None and LEVEL[role] >= LEVEL[minimum]


def role_required(minimum: str):
    """View decorator: login first, then at least `minimum` role, else 403."""
    def decorator(view):
        @wraps(view)
        def wrapper(request, *args, **kwargs):
            if not request.user.is_authenticated:
                return redirect_to_login(request.get_full_path())
            if not has_role(request.user, minimum):
                raise PermissionDenied(f"This needs the {minimum} role.")
            return view(request, *args, **kwargs)
        return wrapper
    return decorator


class HasRole(BasePermission):
    """DRF: GET needs Viewer; anything that changes data needs Agent."""
    def has_permission(self, request, view):
        needed = VIEWER if request.method in ('GET', 'HEAD', 'OPTIONS') else AGENT
        return has_role(request.user, needed)


def set_role(user, role: str) -> None:
    """Admins also get Django Admin access; Agents/Viewers only use the dashboard."""
    from django.contrib.auth.models import Group
    user.groups.remove(*Group.objects.filter(name__in=ROLES))
    user.groups.add(Group.objects.get_or_create(name=role)[0])
    user.is_staff = user.is_superuser = (role == ADMIN)
    user.save(update_fields=['is_staff', 'is_superuser'])
    user.__dict__.pop('_dashboard_role', None)
