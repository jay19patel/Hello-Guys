from django.contrib import admin

from pms.models import Project, Task


@admin.register(Project)
class ProjectAdmin(admin.ModelAdmin):
    list_display = ("name", "status", "owner", "start_date", "due_date", "created_at")
    list_filter = ("status", "created_at")
    search_fields = ("name", "description")
    list_select_related = ("owner",)
    filter_horizontal = ("members",)


@admin.register(Task)
class TaskAdmin(admin.ModelAdmin):
    list_display = ("title", "project", "parent", "is_group", "status", "priority", "assignee", "due_date")
    list_filter = ("status", "priority", "is_group", "project")
    search_fields = ("title", "description", "project__name")
    list_select_related = ("project", "parent", "assignee")
    raw_id_fields = ("parent",)
