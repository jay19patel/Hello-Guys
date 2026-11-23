from django import template

from hello_guy_ai.models import Notification

register = template.Library()


@register.simple_tag(takes_context=True)
def hello_guy_unread_count(context) -> int:
    user = getattr(context.get("request"), "user", None)
    if not (user and user.is_authenticated):
        return 0
    return Notification.objects.filter(user=user, is_read=False).count()
