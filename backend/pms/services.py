"""PMS services: bulk outline tree creation."""

from datetime import date
from decimal import Decimal, InvalidOperation

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction

from pms.models import Project, Task

MAX_TREE_ROWS = 200


@transaction.atomic
def create_tree(project: Project, parent: Task | None, rows: list[dict]) -> list[Task]:
    """Create a tree of tasks in outline order, all-or-nothing."""
    if parent is not None and parent.project_id != project.pk:
        raise ValidationError("Parent task must belong to this project.")
    if not isinstance(rows, list) or not rows:
        raise ValidationError("Provide at least one task row.")
    if len(rows) > MAX_TREE_ROWS:
        raise ValidationError(f"At most {MAX_TREE_ROWS} tasks can be added at once.")

    User = get_user_model()
    active_users = {str(u.pk): u for u in User.objects.filter(is_active=True)}

    stack: list[Task | None] = [parent]
    created: list[Task] = []

    for idx, raw in enumerate(rows, start=1):
        if not isinstance(raw, dict):
            raise ValidationError(f"Row {idx}: invalid row format.")

        level = raw.get("level")
        if isinstance(level, bool) or not isinstance(level, int) or level < 0 or level >= len(stack):
            raise ValidationError(f"Row {idx}: invalid indent level (can indent at most one level deeper than the row above).")

        title = str(raw.get("title") or "").strip()
        if not title:
            raise ValidationError(f"Row {idx}: title is required.")
        if len(title) > 255:
            raise ValidationError(f"Row {idx}: title must be 255 characters or fewer.")

        assignee = None
        raw_assignee = raw.get("assignee")
        if raw_assignee not in (None, ""):
            assignee = active_users.get(str(raw_assignee))
            if assignee is None:
                raise ValidationError(f"Row {idx}: assignee must be an active user.")

        due_date = None
        raw_due = str(raw.get("due_date") or "").strip()
        if raw_due:
            try:
                due_date = date.fromisoformat(raw_due)
            except ValueError:
                raise ValidationError(f"Row {idx}: invalid due date.") from None

        hours = Decimal("0.00")
        raw_hours = raw.get("hours")
        if raw_hours not in (None, ""):
            try:
                hours = Decimal(str(raw_hours))
            except (InvalidOperation, ValueError):
                raise ValidationError(f"Row {idx}: estimated hours must be a number.") from None
            if hours < 0:
                raise ValidationError(f"Row {idx}: estimated hours cannot be negative.")

        row_parent = stack[level]
        if row_parent is not None and not row_parent.is_group:
            row_parent.is_group = True
            row_parent.save(update_fields=["is_group", "updated_at"])

        task = Task(
            project=project,
            parent=row_parent,
            title=title,
            assignee=assignee,
            due_date=due_date,
            expected_hours=hours,
        )
        try:
            task.full_clean()
        except ValidationError as exc:
            msg = "; ".join(exc.messages)
            raise ValidationError(f"Row {idx}: {msg}") from None

        task.save()
        created.append(task)
        stack = stack[: level + 1] + [task]

    return created
