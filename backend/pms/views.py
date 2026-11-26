"""PMS views (/pms/). Roles from meta_connector.roles: Viewer reads, Agent creates/edits, Admin sees all."""

from decimal import Decimal
import json

from django.contrib import messages
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db.models import Count, F, Q, RestrictedError, Sum
from django.db.models.functions import Coalesce
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.utils import timezone
from django.views.generic import CreateView, DetailView, ListView

from meta_connector.roles import ADMIN, AGENT, RoleRequiredMixin, get_role, has_role
from pms import services
from pms.forms import ProjectForm, TaskForm
from pms.models import Project, Task

PAGE_SIZE = 25


class PMSView(RoleRequiredMixin):
    """Role check + shared template context for PMS."""

    def get_context_data(self, **kwargs):
        user = self.request.user
        return super().get_context_data(
            pms_role=get_role(user),
            can_edit=has_role(user, AGENT),
            is_admin=has_role(user, ADMIN),
            today=timezone.localdate(),
            **kwargs,
        )


class ActionsMixin:
    """POST action=<name> runs post_<name>() (dashes become underscores)."""

    def post(self, request, *args, **kwargs):
        if hasattr(self, "get_object"):
            self.object = self.get_object()
        handler = getattr(self, f"post_{request.POST.get('action', '').replace('-', '_')}", None)
        if handler is None:
            return HttpResponseBadRequest("Unknown action")
        return handler()

    def rerender(self, **forms):
        return self.render_to_response(self.get_context_data(**forms))


def compute_builder_slots(rows: list[dict]) -> dict[str, dict[str, int]]:
    """Compute CSS flex order & indent for the inline bulk tree builder under every task and at top level."""
    slots: dict[str, dict[str, int]] = {"": {"order": 2 * len(rows) + 1, "indent": 0}}
    for i, row in enumerate(rows):
        depth = row["depth"]
        end = i
        j = i + 1
        while j < len(rows) and rows[j]["depth"] > depth:
            end = j
            j += 1
        slots[str(row["task"].pk)] = {"order": 2 * end + 3, "indent": (depth + 1) * 24}
    return slots


class ProjectListView(PMSView, ListView):
    template_name = "pms/project_list.html"
    context_object_name = "projects"
    paginate_by = PAGE_SIZE

    def get_queryset(self):
        qs = (
            Project.objects.visible_to(self.request.user)
            .select_related("owner")
            .annotate(
                total_tasks=Count("tasks", distinct=True),
                done_tasks=Count("tasks", filter=Q(tasks__status=Task.Status.DONE), distinct=True),
                total_expected_hours=Coalesce(Sum("tasks__expected_hours"), Decimal("0.00")),
                total_actual_hours=Coalesce(Sum("tasks__actual_hours"), Decimal("0.00")),
            )
        )
        q = self.request.GET.get("q", "").strip()
        status = self.request.GET.get("status", "").strip()
        if q:
            qs = qs.filter(name__icontains=q)
        if status in Project.Status.values:
            qs = qs.filter(status=status)
        return qs

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            q=self.request.GET.get("q", "").strip(),
            status_filter=self.request.GET.get("status", "").strip(),
            status_choices=Project.Status.choices,
            **kwargs,
        )


class ProjectCreateView(PMSView, CreateView):
    role = AGENT
    form_class = ProjectForm
    template_name = "pms/project_form.html"
    extra_context = {"title": "New project"}

    def form_valid(self, form):
        form.instance.owner = self.request.user
        project = form.save()
        messages.success(self.request, f"Project \"{project.name}\" created.")
        return redirect("pms:project", project.pk)

    def get_context_data(self, **kwargs):
        return super().get_context_data(back=reverse("pms:projects"), **kwargs)


class ProjectDetailView(PMSView, ActionsMixin, DetailView):
    template_name = "pms/project_detail.html"
    context_object_name = "project"

    def get_queryset(self):
        return Project.objects.visible_to(self.request.user).select_related("owner").prefetch_related("members")

    def get_context_data(self, **kwargs):
        project = self.object
        rows = project.task_rows()
        for idx, r in enumerate(rows):
            r["order"] = 2 * idx + 2

        total_tasks = len(rows)
        done_tasks = sum(1 for r in rows if r["task"].status == Task.Status.DONE)
        project_progress = round((done_tasks / total_tasks) * 100) if total_tasks else 0
        total_expected_hours = sum((r["task"].expected_hours or Decimal("0.00") for r in rows), Decimal("0.00"))
        total_actual_hours = sum((r["task"].actual_hours or Decimal("0.00") for r in rows), Decimal("0.00"))

        slots = compute_builder_slots(rows)
        group_ids = [r["task"].pk for r in rows if r["task"].is_group or r["child_count"] > 0]
        parent_options = [{"id": str(r["task"].pk), "label": ("— " * r["depth"]) + r["task"].title} for r in rows]
        User = get_user_model()
        users = [
            {"id": str(u.pk), "name": u.get_full_name() or u.get_username()}
            for u in User.objects.filter(is_active=True).order_by("username")
        ]

        kwargs.setdefault("form", ProjectForm(instance=project))
        kwargs.setdefault("bulk_draft", {"rows": [], "anchor": ""})

        return super().get_context_data(
            rows=rows,
            slots=slots,
            group_ids=group_ids,
            parent_options=parent_options,
            users=users,
            total_tasks=total_tasks,
            done_tasks=done_tasks,
            project_progress=project_progress,
            total_expected_hours=total_expected_hours,
            total_actual_hours=total_actual_hours,
            can_delete=has_role(self.request.user, ADMIN) or project.owner_id == self.request.user.pk,
            **kwargs,
        )

    def post_update(self):
        form = ProjectForm(self.request.POST, instance=self.object)
        if not form.is_valid():
            return self.rerender(form=form)
        form.save()
        messages.success(self.request, "Project updated.")
        return redirect("pms:project", self.object.pk)

    def post_bulk(self):
        raw_tree = self.request.POST.get("tree", "")
        anchor = self.request.POST.get("parent", "").strip()
        parent = None
        if anchor:
            parent = Task.objects.filter(project=self.object, pk=anchor).first()
            if parent is None:
                return self.rerender(
                    bulk_error="Selected parent task was not found in this project.",
                    bulk_draft={"rows": [], "anchor": ""},
                )
        try:
            rows = json.loads(raw_tree or "[]")
        except (ValueError, TypeError):
            return self.rerender(
                bulk_error="Invalid task outline data. Please check your rows and try again.",
                bulk_draft={"rows": [], "anchor": anchor},
            )

        try:
            created = services.create_tree(self.object, parent, rows)
        except ValidationError as exc:
            msg = "; ".join(exc.messages)
            return self.rerender(
                bulk_error=msg,
                bulk_draft={"rows": rows if isinstance(rows, list) else [], "anchor": anchor},
            )

        messages.success(self.request, f"{len(created)} task{'s' if len(created) != 1 else ''} created.")
        return redirect(reverse("pms:project", args=[self.object.pk]) + f"#task-{created[0].pk}")

    def post_delete(self):
        if not (has_role(self.request.user, ADMIN) or self.object.owner_id == self.request.user.pk):
            raise PermissionDenied("Only an Admin or the project owner can delete this project.")
        name = self.object.name
        self.object.delete()
        messages.success(self.request, f"Project \"{name}\" deleted.")
        return redirect("pms:projects")


class TaskCreateView(PMSView, CreateView):
    role = AGENT
    form_class = TaskForm
    template_name = "pms/task_form.html"

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated and has_role(request.user, AGENT):
            self.project = get_object_or_404(Project.objects.visible_to(request.user), pk=kwargs["pk"])
        else:
            self.project = None
        return super().dispatch(request, *args, **kwargs)

    def get_form_kwargs(self):
        kw = super().get_form_kwargs()
        kw["project"] = self.project
        return kw

    def get_initial(self):
        initial = {"assignee": self.request.user}
        if parent_id := self.request.GET.get("parent"):
            parent = Task.objects.filter(project=self.project, pk=parent_id, is_group=True).first()
            if parent:
                initial["parent"] = parent
        return initial

    def form_valid(self, form):
        task = form.save()
        messages.success(self.request, f"Task \"{task.title}\" created.")
        if "add_another" in self.request.POST:
            url = reverse("pms:task-create", args=[self.project.pk])
            if task.parent_id:
                url += f"?parent={task.parent_id}"
            return redirect(url)
        return redirect(reverse("pms:project", args=[self.project.pk]) + f"#task-{task.pk}")

    def get_context_data(self, **kwargs):
        return super().get_context_data(
            project=self.project,
            title=f"New task · {self.project.name}",
            back=reverse("pms:project", args=[self.project.pk]),
            **kwargs,
        )


class TaskDetailView(PMSView, ActionsMixin, DetailView):
    template_name = "pms/task_detail.html"
    context_object_name = "task"

    def get_queryset(self):
        visible_projects = Project.objects.visible_to(self.request.user)
        return Task.objects.filter(project__in=visible_projects).select_related("project", "parent", "assignee")

    def get_context_data(self, **kwargs):
        task = self.object
        ancestor_pks = task.ancestor_ids()
        ancestors_map = Task.objects.in_bulk(ancestor_pks)
        ancestors = [ancestors_map[pk] for pk in ancestor_pks if pk in ancestors_map]
        subtasks = list(task.children.select_related("assignee").order_by("created_at", "pk"))
        kwargs.setdefault("form", TaskForm(project=task.project, instance=task))
        return super().get_context_data(
            project=task.project,
            ancestors=ancestors,
            subtasks=subtasks,
            has_children=bool(subtasks),
            status_choices=Task.Status.choices,
            **kwargs,
        )

    def post_update(self):
        form = TaskForm(self.request.POST, project=self.object.project, instance=self.object)
        if not form.is_valid():
            return self.rerender(form=form)
        form.save()
        messages.success(self.request, "Task updated.")
        return redirect("pms:task", self.object.pk)

    def post_status(self):
        new_status = self.request.POST.get("status", "").strip()
        if new_status not in Task.Status.values:
            return HttpResponseBadRequest("Invalid status")
        self.object.status = new_status
        self.object.save(update_fields=["status", "updated_at"])
        messages.success(self.request, f"Status set to {self.object.get_status_display()}.")
        return redirect("pms:task", self.object.pk)

    def post_delete(self):
        project_pk = self.object.project_id
        try:
            self.object.delete()
        except RestrictedError:
            messages.error(self.request, "Cannot delete a task that has subtasks. Delete or move subtasks first.")
            return redirect("pms:task", self.object.pk)
        messages.success(self.request, "Task deleted.")
        return redirect("pms:project", project_pk)


class MyTasksView(PMSView, ListView):
    template_name = "pms/my_tasks.html"
    context_object_name = "tasks"
    paginate_by = PAGE_SIZE

    def get_queryset(self):
        visible_projects = Project.objects.visible_to(self.request.user)
        return (
            Task.objects.filter(project__in=visible_projects, assignee=self.request.user)
            .exclude(status__in=[Task.Status.DONE, Task.Status.CANCELLED])
            .select_related("project", "parent")
            .order_by(F("due_date").asc(nulls_last=True), "created_at", "pk")
        )
