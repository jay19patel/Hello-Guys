from meta_connector.models import PendingAIReply, PlatformAccount
from meta_connector.roles import ADMIN, AGENT, get_role, has_role
from meta_connector.services.maintenance import webhook_health

def dashboard_nav(request):
    # Runs on every template render; the account list is evaluated eagerly below.
    user = getattr(request, 'user', None)
    admin = bool(user) and has_role(user, ADMIN)

    # Active accounts grouped dynamically by platform for any current or future channel
    active_accounts = list(
        PlatformAccount.objects.filter(is_active=True).order_by('platform', '-created_at')
    )

    platform_order = [
        PlatformAccount.PLATFORM_WHATSAPP,
        PlatformAccount.PLATFORM_INSTAGRAM,
        PlatformAccount.PLATFORM_FACEBOOK,
    ]
    platform_names = {
        PlatformAccount.PLATFORM_WHATSAPP: 'WhatsApp',
        PlatformAccount.PLATFORM_INSTAGRAM: 'Instagram',
        PlatformAccount.PLATFORM_FACEBOOK: 'Facebook Messenger',
    }

    grouped = {}
    for acc in active_accounts:
        grouped.setdefault(acc.platform, []).append(acc)

    def sort_key(p):
        return platform_order.index(p) if p in platform_order else 99

    nav_account_groups = [
        {
            'platform': p,
            'name': platform_names.get(p, p.replace('_', ' ').title()),
            'accounts': accs,
        }
        for p, accs in sorted(grouped.items(), key=lambda item: sort_key(item[0]))
    ]

    approvals_pending = PendingAIReply.objects.filter(status=PendingAIReply.STATUS_PENDING).count() if user and user.is_authenticated else 0

    return {
        # Failed-webhook / bad-signature banner: admins only, since only they can fix or retry.
        'webhook_health': webhook_health() if admin else None,
        'nav_account_groups': nav_account_groups,
        'nav_whatsapp_accounts': [a for a in active_accounts if a.platform == PlatformAccount.PLATFORM_WHATSAPP],
        'nav_instagram_accounts': [a for a in active_accounts if a.platform == PlatformAccount.PLATFORM_INSTAGRAM],
        'approvals_pending': approvals_pending,
        # Templates use these only to hide controls; every view checks the role again on the server.
        'dashboard_role': get_role(user) if user else None,
        'can_reply': bool(user) and has_role(user, AGENT),
        'is_admin': admin,
    }
