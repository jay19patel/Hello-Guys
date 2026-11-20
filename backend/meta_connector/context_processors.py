from meta_connector.models import PlatformAccount
from meta_connector.roles import ADMIN, AGENT, get_role, has_role
from meta_connector.services.maintenance import webhook_health


def dashboard_nav(request):
    # Lazy queryset: only hits the DB on pages that render the sidebar.
    user = getattr(request, 'user', None)
    admin = bool(user) and has_role(user, ADMIN)
    return {
        # Failed-webhook / bad-signature banner: admins only, since only they can fix or retry.
        'webhook_health': webhook_health() if admin else None,
        'nav_instagram_accounts': PlatformAccount.objects.filter(platform=PlatformAccount.PLATFORM_INSTAGRAM, is_active=True),
        # Templates use these only to hide controls; every view checks the role again on the server.
        'dashboard_role': get_role(user) if user else None,
        'can_reply': bool(user) and has_role(user, AGENT),
        'is_admin': admin,
    }
