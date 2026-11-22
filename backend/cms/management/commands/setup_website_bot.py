"""Create (or repair) the user the public website chat runs as. Safe to run any number of times.

    python manage.py setup_website_bot

The user can VIEW public site content and ADD inquiries. Nothing else: it cannot create, change or
delete blogs or any other data, cannot read inquiries, cannot log in (no password), and is not staff.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

from hello_guy_ai.conf import ai_settings

GROUP = "Website Visitor"
PERMISSIONS = [
    "view_blogpost",
    "view_project",
    "view_faq",
    "view_solution",
    "view_testimonial",
    "view_techskill",
    "view_youtubevideo",
    "add_inquirysubmission",  # the only write: visitors can leave a lead
]


class Command(BaseCommand):
    help = "Create the website chat user and its view-only + add-inquiry group"

    def handle(self, *args, **options):
        username = ai_settings.PUBLIC_USER
        if not username:
            self.stderr.write('Set HELLO_GUY_AI["PUBLIC_USER"] in settings first.')
            return

        perms = list(Permission.objects.filter(content_type__app_label="cms", codename__in=PERMISSIONS))
        missing = set(PERMISSIONS) - {p.codename for p in perms}
        if missing:
            self.stderr.write(f"Missing permissions (run migrate first): {sorted(missing)}")
            return
        group, _ = Group.objects.get_or_create(name=GROUP)
        group.permissions.set(perms)  # exact set: removes anything added by hand

        user, created = get_user_model().objects.get_or_create(username=username)
        user.is_active, user.is_staff, user.is_superuser = True, False, False
        user.set_unusable_password()  # never logs in; the public API acts as this user
        user.save()
        user.user_permissions.clear()
        user.groups.set([group])
        self.stdout.write(self.style.SUCCESS(f"{'Created' if created else 'Updated'} user {username!r} in group {GROUP!r}"))
