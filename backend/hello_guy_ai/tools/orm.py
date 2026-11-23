"""Built-in tools that let an agent operate your Django data (Flow: find_doctypes/describe/read/create/update/delete).

Security model (every call runs as the *requesting* user, from the run context):
  1. Model allowlist   settings.HELLO_GUY_AI["ORM_TOOLS_MODELS"] = ["crm.lead", ...]. Default: none.
  2. Django perms      view/add/change/delete_<model> checked on every call.
  3. Row scope         optional classmethod Model.hello_guy_queryset(user) narrows rows (tenancy).
  4. Field filter      sensitive-looking fields (password, token, secret...) are invisible + unwritable.
  5. No relation hops  filters like owner__password__startswith are rejected.
  6. Writes pause      create/update/delete require human approval unless the run auto-approves.
  7. Deletes off       db_delete is refused unless HELLO_GUY_AI["ALLOW_DELETE"] is True.
"""

import json
from typing import Any, Literal

from django.apps import apps
from django.contrib.auth import get_permission_codename
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models import Avg, Count, Max, Min, Sum

from hello_guy_ai.conf import ai_settings
from hello_guy_ai.core.runtime import SENSITIVE_FIELD_PATTERN as SENSITIVE
from hello_guy_ai.core.runtime import ToolError, current_context, tool

ALLOWED_LOOKUPS = {
    "exact",
    "iexact",
    "contains",
    "icontains",
    "in",
    "gt",
    "gte",
    "lt",
    "lte",
    "startswith",
    "istartswith",
    "endswith",
    "iendswith",
    "isnull",
    "range",
    "date",
    "year",
    "month",
}
MAX_READ = 100
MAX_WRITE = 50
AGGREGATES = {"count": Count, "sum": Sum, "avg": Avg, "min": Min, "max": Max}


@tool
def db_list_models() -> list[dict]:
    """List the data models you may work with. Call this first; never guess model names."""
    user = current_context().user
    return [
        {"model": label, "name": str(model._meta.verbose_name_plural)}
        for label, model in _allowed_models().items()
        if _has_perm(user, model, "view")
    ]


@tool
def db_describe(model: str) -> dict:
    """Show a model's fields (name, type, required, choices, relation) and your permissions on it."""
    m = _model(model)
    user = current_context().user
    _require(user, m, "view")
    fields = []
    for f in _unique(_fields(m)):
        info: dict[str, Any] = {"name": f.attname, "type": f.get_internal_type(), "required": _required(f)}
        if f.choices:
            info["choices"] = [c[0] for c in f.choices]
        if f.is_relation and f.related_model:
            info["references"] = f.related_model._meta.label_lower
        if getattr(f, "max_length", None):
            info["max_length"] = f.max_length
        fields.append(info)
    perms = {a: _has_perm(user, m, a) for a in ("view", "add", "change", "delete")}
    return {"model": model, "fields": fields, "permissions": perms}


@tool
def db_read(
    model: str,
    filters: dict[str, Any] | None = None,
    fields: list[str] | None = None,
    order_by: str | None = None,
    limit: int = 20,
) -> dict:
    """Read records. filters: {"status": "new", "amount__gte": 100, "name__icontains": "acme"}.
    fields: columns to return (default: all visible). order_by: field or -field. limit max 100."""
    m = _model(model)
    user = current_context().user
    _require(user, m, "view")
    allowed = _fields(m)
    columns = [_column(allowed, c) for c in fields] if fields else [f.attname for f in _unique(allowed)]
    qs = _queryset(m, user).filter(**_clean_filters(allowed, filters or {}))
    if order_by:
        qs = qs.order_by(("-" if order_by.startswith("-") else "") + _column(allowed, order_by.lstrip("-")))
    limit = min(max(int(limit), 1), MAX_READ)
    rows = list(qs.values("pk", *columns)[: limit + 1])
    return {"model": model, "records": rows[:limit], "truncated": len(rows) > limit}


@tool
def db_aggregate(
    model: str,
    metric: Literal["count", "sum", "avg", "min", "max"],
    field: str | None = None,
    filters: dict[str, Any] | None = None,
    group_by: str | None = None,
    order: Literal["desc", "asc"] = "desc",
    limit: int = 10,
) -> dict:
    """Totals and rankings. metric over field (field not needed for count), optional filters like db_read.
    group_by: a field (use <fk>_id for relations) -> one row per value, sorted by the metric.
    Example "top 5 customers by revenue last month": model=shop.order, metric=sum, field=total,
    filters={"created_at__gte": "2026-10-01", "created_at__lt": "2026-11-01"}, group_by=customer_id, limit=5."""
    m = _model(model)
    user = current_context().user
    _require(user, m, "view")
    allowed = _fields(m)
    if metric != "count" and not field:
        raise ToolError(f"metric '{metric}' needs a field")
    value = AGGREGATES[metric](_column(allowed, field) if field else "pk")
    qs = _queryset(m, user).filter(**_clean_filters(allowed, filters or {}))
    if not group_by:
        return {"model": model, "metric": metric, "field": field, "value": qs.aggregate(value=value)["value"]}
    key = _column(allowed, group_by)
    limit = min(max(int(limit), 1), MAX_READ)
    rows = qs.values(key).annotate(value=value).order_by(("-" if order == "desc" else "") + "value")[:limit]
    return {"model": model, "metric": metric, "field": field, "group_by": key, "rows": list(rows)}


def _confirm_create(args: dict) -> str:
    records = args.get("records") or []
    return f"Create {len(records)} {args.get('model')} record(s):\n{_preview(records[:3])}"


def _confirm_update(args: dict) -> str:
    ids = args.get("ids") or []
    return f"Update {len(ids)} {args.get('model')} record(s) {_short(ids)}:\nset {_preview(args.get('values'))}"


def _confirm_delete(args: dict) -> str:
    ids = args.get("ids") or []
    return f"DELETE {len(ids)} {args.get('model')} record(s): {_short(ids)}"


@tool(requires_confirmation=True, confirm_prompt=_confirm_create)
def db_create(model: str, records: list[dict[str, Any]]) -> dict:
    """Create records. records: list of {field: value}; use <fk>_id for relations. Validated before saving."""
    m = _model(model)
    user = current_context().user
    _require(user, m, "add")
    _check_batch(records)
    writable = _writable(m)
    created, failures = [], []
    for i, values in enumerate(records):
        try:
            obj = m(**_clean_values(writable, values))
            with transaction.atomic():
                obj.full_clean()
                obj.save()
            created.append(obj.pk)
        except (ValidationError, ToolError, ValueError, TypeError) as exc:
            failures.append({"row": i, "error": _err(exc)})
    return {"model": model, "created": created, **({"failures": failures} if failures else {})}


@tool(requires_confirmation=True, confirm_prompt=_confirm_update)
def db_update(model: str, ids: list[int | str], values: dict[str, Any]) -> dict:
    """Set the same field values on records by id. Each record is validated before saving."""
    m = _model(model)
    user = current_context().user
    _require(user, m, "change")
    _check_batch(ids)
    clean = _clean_values(_writable(m), values)
    updated, failures = [], []
    objects = {str(o.pk): o for o in _queryset(m, user).filter(pk__in=ids)}
    for pk in ids:
        obj = objects.get(str(pk))
        if obj is None:
            failures.append({"id": pk, "error": "not found or not accessible"})
            continue
        try:
            for name, value in clean.items():
                setattr(obj, name, value)
            with transaction.atomic():
                obj.full_clean()
                obj.save()
            updated.append(obj.pk)
        except (ValidationError, ValueError, TypeError) as exc:
            failures.append({"id": pk, "error": _err(exc)})
    return {"model": model, "updated": updated, **({"failures": failures} if failures else {})}


@tool(requires_confirmation=True, confirm_prompt=_confirm_delete)
def db_delete(model: str, ids: list[int | str]) -> dict:
    """Delete records by id. Irreversible."""
    if not ai_settings.ALLOW_DELETE:
        raise ToolError("Deleting records is disabled by the administrator. Tell the user it must be done manually.")
    m = _model(model)
    user = current_context().user
    _require(user, m, "delete")
    _check_batch(ids)
    found = list(_queryset(m, user).filter(pk__in=ids))
    deleted = []
    with transaction.atomic():  # all or nothing: never leave a half-deleted batch
        for obj in found:
            pk = obj.pk
            obj.delete()  # per object so signals and on_delete rules run
            deleted.append(pk)
    missing = [i for i in ids if str(i) not in {str(d) for d in deleted}]
    return {"model": model, "deleted": deleted, **({"not_found": missing} if missing else {})}


# ---- helpers ---------------------------------------------------------------


def _allowed_models() -> dict[str, type[models.Model]]:
    out = {}
    for label in ai_settings.ORM_TOOLS_MODELS:
        try:
            out[label.lower()] = apps.get_model(label)
        except (LookupError, ValueError):
            continue
    return out


def _model(label: str) -> type[models.Model]:
    model = _allowed_models().get((label or "").lower())
    if model is None:
        raise ToolError(f"Unknown model '{label}'. Call db_list_models for valid names.")
    return model


def _has_perm(user: Any, model: type[models.Model], action: str) -> bool:
    return user.has_perm(f"{model._meta.app_label}.{get_permission_codename(action, model._meta)}")


def _require(user: Any, model: type[models.Model], action: str) -> None:
    if not _has_perm(user, model, action):
        raise ToolError(f"You do not have permission to {action} {model._meta.label_lower}.")


def _queryset(model: type[models.Model], user: Any) -> models.QuerySet:
    hook = getattr(model, "hello_guy_queryset", None)
    return hook(user) if callable(hook) else model._default_manager.all()


def _fields(model: type[models.Model]) -> dict[str, models.Field]:
    """Visible concrete fields, keyed by both name and attname (fk and fk_id)."""
    out: dict[str, models.Field] = {}
    for f in model._meta.concrete_fields:
        if SENSITIVE.search(f.name):
            continue
        out[f.name] = f
        out[f.attname] = f
    return out


def _unique(fields: dict[str, models.Field]) -> list[models.Field]:
    return list(dict.fromkeys(fields.values()))


def _writable(model: type[models.Model]) -> dict[str, models.Field]:
    return {k: f for k, f in _fields(model).items() if f.editable and not f.primary_key and not f.auto_created}


def _column(allowed: dict[str, models.Field], name: str) -> str:
    field = allowed.get(name)
    if field is None:
        raise ToolError(f"Unknown field '{name}'. Call db_describe for valid fields.")
    return field.attname


def _clean_filters(allowed: dict[str, models.Field], filters: dict[str, Any]) -> dict[str, Any]:
    clean = {}
    for key, value in filters.items():
        parts = key.split("__")
        if len(parts) > 2:
            raise ToolError(f"Filter '{key}': relation traversal is not allowed; filter on <fk>_id instead.")
        lookup = parts[1] if len(parts) == 2 else "exact"
        if lookup not in ALLOWED_LOOKUPS:
            raise ToolError(f"Filter '{key}': lookup '{lookup}' not allowed. Use one of {sorted(ALLOWED_LOOKUPS)}.")
        if lookup == "in" and (not isinstance(value, list) or len(value) > MAX_READ):
            raise ToolError(f"Filter '{key}': 'in' needs a list of at most {MAX_READ} values.")
        clean[f"{_column(allowed, parts[0])}__{lookup}"] = value
    return clean


def _clean_values(writable: dict[str, models.Field], values: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(values, dict) or not values:
        raise ToolError("values must be a non-empty object of {field: value}")
    unknown = [k for k in values if k not in writable]
    if unknown:
        raise ToolError(f"Fields not writable or unknown: {unknown}. Call db_describe.")
    return {writable[k].attname: v for k, v in values.items()}


def _required(field: models.Field) -> bool:
    return not field.blank and not field.has_default() and not field.null and not field.auto_created


def _check_batch(items: list) -> None:
    if not items:
        raise ToolError("Nothing to do: empty list")
    if len(items) > MAX_WRITE:
        raise ToolError(f"At most {MAX_WRITE} records per call")


def _err(exc: Exception) -> str:
    if isinstance(exc, ValidationError) and hasattr(exc, "message_dict"):
        return json.dumps(exc.message_dict)
    return str(exc)[:300]


def _preview(value: Any) -> str:
    text = json.dumps(value, indent=2, default=str, ensure_ascii=False)
    return text if len(text) <= 800 else text[:800] + "\n…"


def _short(ids: list) -> str:
    shown = ", ".join(str(i) for i in ids[:8])
    return shown + (f" … +{len(ids) - 8} more" if len(ids) > 8 else "")
