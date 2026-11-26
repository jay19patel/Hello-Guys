"""Create (or repair) the user the public website chat runs as. Safe to run any number of times.

    python manage.py setup_website_bot

The user can VIEW public site content and ADD inquiries (a CRM Customer + Opportunity). Nothing else: it cannot
create, change or delete blogs or any other data, cannot read inquiries, cannot log in (no password), and is not staff.
"""

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group, Permission
from django.core.management.base import BaseCommand

from hello_guy_ai.conf import ai_settings

GROUP = "Website Visitor"
PERMISSIONS = [
    ("cms", "view_blogpost"),
    ("cms", "view_project"),
    ("cms", "view_faq"),
    ("cms", "view_solution"),
    ("cms", "view_testimonial"),
    ("cms", "view_techskill"),
    ("cms", "view_youtubevideo"),
    ("crm", "add_opportunity"),  # the only writes: visitors can leave a lead (create_inquiry)
    ("crm", "add_customer"),
]


class Command(BaseCommand):
    help = "Create the website chat user and its view-only + add-inquiry group"

    def handle(self, *args, **options):
        username = ai_settings.PUBLIC_USER
        if not username:
            self.stderr.write('Set HELLO_GUY_AI["PUBLIC_USER"] in settings first.')
            return

        perms = [
            p
            for p in Permission.objects.filter(
                content_type__app_label__in={app for app, _ in PERMISSIONS}, codename__in={c for _, c in PERMISSIONS}
            ).select_related("content_type")
            if (p.content_type.app_label, p.codename) in PERMISSIONS
        ]
        missing = set(PERMISSIONS) - {(p.content_type.app_label, p.codename) for p in perms}
        if missing:
            self.stderr.write(f"Missing permissions (run migrate first): {sorted(missing)}")
            return
        group, _ = Group.objects.get_or_create(name=GROUP)
        group.permissions.set(perms)  # exact set: removes anything added by hand

        user, created = get_user_model().objects.get_or_create(username=username)
        user.is_active, user.is_staff, user.is_superuser = True, False, False
        user.first_name = user.first_name or "AI assistant"  # how its replies are labelled in the Meta dashboard
        user.set_unusable_password()  # never logs in; the public API acts as this user
        user.save()
        user.user_permissions.clear()
        user.groups.set([group])
        self.stdout.write(self.style.SUCCESS(f"{'Created' if created else 'Updated'} user {username!r} in group {GROUP!r}"))
