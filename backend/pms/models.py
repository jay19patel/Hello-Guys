"""PMS: Projects and a tree of Tasks/Subtasks (adjacency list)."""

from decimal import Decimal

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.db import models
from django.utils import timezone

from meta_connector.roles import ADMIN, has_role


class TimeStamped(models.Model):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


class ProjectQuerySet(models.QuerySet):
    def visible_to(self, user) -> "ProjectQuerySet":
        if not getattr(user, "is_authenticated", False):
            return self.none()
        if has_role(user, ADMIN):
            return self
        # a subquery, not a join on members: joining multiplies task counts and hour sums per member
        member_of = self.model.members.through.objects.filter(user=user).values("project_id")
        return self.filter(models.Q(owner=user) | models.Q(pk__in=member_of))


class Project(TimeStamped):
    class Status(models.TextChoices):
        ACTIVE = "active", "Active"
        ON_HOLD = "on_hold", "On hold"
        COMPLETED = "completed", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ACTIVE, db_index=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="owned_projects"
    )
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, blank=True, related_name="pms_projects")
    start_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)

    objects = ProjectQuerySet.as_manager()

    class Meta:
        ordering = ("-created_at",)

    def __str__(self) -> str:
        return self.name

    def task_rows(self) -> list[dict]:
        """Flatten the project's task tree depth-first in one query, with subtree roll-ups."""
        tasks = list(self.tasks.select_related("assignee").order_by("created_at", "pk"))
        children_by_parent: dict[int | None, list[Task]] = {}
        for t in tasks:
            children_by_parent.setdefault(t.parent_id, []).append(t)

        rows: list[dict] = []
        seen: set[int] = set()

        def walk(task: Task, depth: int, ancestors: list[int]) -> tuple[int, int, Decimal, Decimal, list]:
            if task.pk in seen:
                return 0, 0, Decimal("0.00"), Decimal("0.00"), []
            seen.add(task.pk)

            idx = len(rows)
            rows.append({})  # reserve depth-first position before recursing

            direct_children = children_by_parent.get(task.pk, [])
            exp_sum = task.expected_hours or Decimal("0.00")
            act_sum = task.actual_hours or Decimal("0.00")
            people: list = [task.assignee] if task.assignee else []
            seen_people: set[int] = {task.assignee_id} if task.assignee_id else set()

            done_leaves = 0
            total_leaves = 0

            if not direct_children:
                if task.status != Task.Status.CANCELLED:
                    total_leaves = 1
                    if task.status == Task.Status.DONE:
                        done_leaves = 1
            else:
                next_ancestors = [*ancestors, task.pk]
                for child in direct_children:
                    c_done, c_total, c_exp, c_act, c_people = walk(child, depth + 1, next_ancestors)
                    done_leaves += c_done
                    total_leaves += c_total
                    exp_sum += c_exp
                    act_sum += c_act
                    for person in c_people:
                        if person.pk not in seen_people:
                            seen_people.add(person.pk)
                            people.append(person)

            progress = round((done_leaves / total_leaves) * 100) if total_leaves else 0
            rows[idx] = {
                "task": task,
                "depth": depth,
                "indent": depth * 24,
                "ancestors": ancestors,
                "child_count": len(direct_children),
                "progress": progress,
                "actual_hours": act_sum,
                "expected_hours": exp_sum,
                "people": people[:3],
            }
            return done_leaves, total_leaves, exp_sum, act_sum, people

        for root in children_by_parent.get(None, []):
            walk(root, 0, [])
        return rows


class Task(TimeStamped):
    class Status(models.TextChoices):
        OPEN = "open", "Open"
        WORKING = "working", "Working"
        REVIEW = "review", "Pending review"
        DONE = "done", "Completed"
        CANCELLED = "cancelled", "Cancelled"

    class Priority(models.TextChoices):
        LOW = "low", "Low"
        MEDIUM = "medium", "Medium"
        HIGH = "high", "High"
        URGENT = "urgent", "Urgent"

    project = models.ForeignKey(Project, on_delete=models.CASCADE, related_name="tasks")
    # Adjacency list (instead of nested-set/MPTT) for simple writes; upgrade if a project exceeds ~10k tasks.
    parent = models.ForeignKey(
        "self", on_delete=models.RESTRICT, null=True, blank=True, related_name="children"
    )
    is_group = models.BooleanField(default=False)
    title = models.CharField(max_length=255)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN, db_index=True)
    priority = models.CharField(max_length=20, choices=Priority.choices, default=Priority.MEDIUM, db_index=True)
    assignee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True, related_name="pms_tasks"
    )
    due_date = models.DateField(null=True, blank=True, db_index=True)
    expected_hours = models.DecimalField(
        "Estimated hours",
        max_digits=7,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
    )
    actual_hours = models.DecimalField(
        "Hours spent",
        max_digits=7,
        decimal_places=2,
        default=Decimal("0.00"),
        validators=[MinValueValidator(Decimal("0"))],
    )

    class Meta:
        ordering = ("created_at", "pk")
        constraints = [
            models.CheckConstraint(condition=~models.Q(parent=models.F("pk")), name="pms_task_not_self_parent"),
        ]

    def __str__(self) -> str:
        return self.title

    @property
    def code(self) -> str:
        if not self.pk:
            return ""
        year = (self.created_at or timezone.now()).year
        prefix = "SUB-TASK" if self.parent_id else "TASK"
        return f"{prefix}-{year}-{self.pk:05d}"

    def _project_links(self) -> tuple[dict[int, int | None], dict[int | None, list[int]]]:
        pairs = list(Task.objects.filter(project_id=self.project_id).values_list("id", "parent_id"))
        parent_of: dict[int, int | None] = {}
        children_of: dict[int | None, list[int]] = {}
        for tid, pid in pairs:
            parent_of[tid] = pid
            children_of.setdefault(pid, []).append(tid)
        return parent_of, children_of

    def ancestor_ids(self, start_id: int | None = None) -> list[int]:
        """Ordered list of ancestor IDs from root down to `start_id` (or `self.parent_id`)."""
        parent_of, _ = self._project_links()
        cur = self.parent_id if start_id is None else start_id
        chain: list[int] = []
        seen: set[int] = set()
        while cur is not None and cur not in seen:
            seen.add(cur)
            chain.append(cur)
            cur = parent_of.get(cur)
        chain.reverse()
        return chain

    def descendant_ids(self) -> set[int]:
        """All descendant IDs of this task within its project."""
        if not self.pk:
            return set()
        _, children_of = self._project_links()
        found: set[int] = set()
        stack = list(children_of.get(self.pk, []))
        while stack:
            cid = stack.pop()
            if cid not in found:
                found.add(cid)
                stack.extend(children_of.get(cid, []))
        return found

    def clean(self) -> None:
        if self.pk:
            old_project_id = Task.objects.filter(pk=self.pk).values_list("project_id", flat=True).first()
            has_children = Task.objects.filter(parent_id=self.pk).exists()
            if has_children and not self.is_group:
                raise ValidationError({"is_group": "A task with subtasks must remain a group."})
            if has_children and old_project_id is not None and self.project_id != old_project_id:
                raise ValidationError({"project": "Move or remove subtasks before changing project."})

        if self.parent_id is not None:
            if self.pk and self.parent_id == self.pk:
                raise ValidationError({"parent": "A task cannot be its own parent."})
            if self.parent.project_id != self.project_id:
                raise ValidationError({"parent": "Parent task must be in the same project."})
            if not self.parent.is_group:
                raise ValidationError({"parent": "Parent task must be a group."})
            if self.pk and self.parent_id in self.descendant_ids():
                raise ValidationError({"parent": "Cannot move a task under one of its own subtasks."})
