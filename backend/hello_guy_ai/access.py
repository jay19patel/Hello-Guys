"""Who may use the internal chat page and /api/ai/ (the public website chat is separate and anonymous)."""

from ninja.security import django_auth


def can_chat(user) -> bool:
    """Staff, or anyone with a dashboard role (Viewer and up). A role-less account cannot reach the assistants."""
    from meta_connector.roles import VIEWER, has_role  # host app owns the roles

    return bool(user.is_authenticated and (user.is_staff or has_role(user, VIEWER)))


def chat_auth(request):
    """Ninja auth for the internal API: logged in AND allowed to chat."""
    user = django_auth(request)
    return user if user and can_chat(user) else None
