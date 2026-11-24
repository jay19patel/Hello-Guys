"""
Dashboard roles, stored as Django groups:
  Viewer - can look at everything except Settings; changes nothing.
  Agent  - Viewer + reply to contacts and set auto-DM messages. Cannot delete anything or open Settings.
  Admin  - everything: Settings, accounts, tokens, team, deleting, Django Admin.
Superusers are always Admin. A user with no role cannot open the dashboard.
"""
import logging

from django.contrib.auth import get_user_model
from django.contrib.auth.mixins import AccessMixin
from django.core.exceptions import PermissionDenied
from django.db.models import Q
from rest_framework.permissions import BasePermission

logger = logging.getLogger(__name__)

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


class RoleRequiredMixin(AccessMixin):
    """Class-based views: `role` to open the page, `edit_role` to POST. Anonymous users go to the login page."""
    role = VIEWER
    edit_role = AGENT

    def dispatch(self, request, *args, **kwargs):
        if not request.user.is_authenticated:
            return self.handle_no_permission()
        needed = self.role if request.method in ('GET', 'HEAD', 'OPTIONS') else self.edit_role
        if not has_role(request.user, needed):
            raise PermissionDenied(f"This needs the {needed} role.")
        return super().dispatch(request, *args, **kwargs)


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


def team():
    """Users who answer customers: superusers and the Admin/Agent roles."""
    return get_user_model().objects.filter(is_active=True).filter(
        Q(is_superuser=True) | Q(groups__name__in=[ADMIN, AGENT])).distinct()


def notify_team(title: str, body: str = "", url: str = "") -> None:
    """In-app notification (email too when hello_guy_ai NOTIFY_EMAIL is on) for every team member."""
    from hello_guy_ai.services.reminders import notify

    for user in team():
        try:
            notify(user, title, body, url=url)
        except Exception:  # an alert must never break the save that triggered it
            logger.exception("Could not notify %s", user)
