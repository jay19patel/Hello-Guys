"""CRM overview analytics and aggregations."""

from datetime import datetime, time, timedelta
from decimal import Decimal
from typing import Any

from django.contrib.auth import get_user_model
from django.db.models import Count, Q, Sum
from django.db.models.functions import TruncDate
from django.utils import timezone

from core.models import FollowUp
from crm.models import Activity, Customer, Meeting, Opportunity, PaymentMilestone, Source
from hello_guy_ai.models import Thread
from meta_connector.models import Contact, PendingAIReply


def _delta(curr: int, prev: int) -> int | None:
    if prev == 0:
        return None if curr == 0 else 100
    return round(((curr - prev) / prev) * 100)


def overview_analytics(user: Any, days: int = 30) -> dict[str, Any]:
    """Compute CRM overview KPIs, local-timezone 14-day trend, funnel, source attribution, and leaderboard."""
    if days not in (7, 30, 90):
        days = 30
    now = timezone.now()
    tz = timezone.get_current_timezone()
    since = now - timedelta(days=days)
    prev_since = since - timedelta(days=days)

    opportunities = Opportunity.objects.select_related("customer", "service", "assigned_to")
    by_status = dict(opportunities.values_list("status").annotate(n=Count("id")))

    act_agg = Activity.objects.filter(created_at__gte=prev_since).aggregate(
        calls_now=Count("id", filter=Q(kind=Activity.Kind.CALL, created_at__gte=since)),
        calls_prev=Count("id", filter=Q(kind=Activity.Kind.CALL, created_at__lt=since)),
        notes_now=Count("id", filter=Q(kind=Activity.Kind.NOTE, created_at__gte=since)),
        notes_prev=Count("id", filter=Q(kind=Activity.Kind.NOTE, created_at__lt=since)),
    )
    meeting_agg = Meeting.objects.aggregate(
        now=Count("id", filter=Q(start_at__gte=since)),
        prev=Count("id", filter=Q(start_at__gte=prev_since, start_at__lt=since)),
        upcoming=Count("id", filter=Q(status=Meeting.Status.SCHEDULED, start_at__gte=now)),
    )
    fu_agg = FollowUp.objects.filter(customer__isnull=False, status=FollowUp.Status.PENDING).aggregate(
        pending=Count("id"),
        overdue=Count("id", filter=Q(due_at__lt=now)),
    )
    fu_pending = fu_agg["pending"] or 0
    fu_overdue = fu_agg["overdue"] or 0

    contact_agg = Contact.objects.aggregate(
        total=Count("id"),
        now=Count("id", filter=Q(first_seen__gte=since)),
        prev=Count("id", filter=Q(first_seen__gte=prev_since, first_seen__lt=since)),
    )
    cust_agg = Customer.objects.aggregate(
        total=Count("id"),
        now=Count("id", filter=Q(created_at__gte=since)),
        prev=Count("id", filter=Q(created_at__gte=prev_since, created_at__lt=since)),
    )
    opp_agg = opportunities.aggregate(
        total=Count("id"),
        open_cnt=Count("id", filter=Q(status__in=Opportunity.OPEN)),
        new_week=Count("id", filter=Q(created_at__gte=now - timedelta(days=7))),
        won_month=Count("id", filter=Q(status=Opportunity.Status.WON, updated_at__gte=now - timedelta(days=30))),
        now=Count("id", filter=Q(created_at__gte=since)),
        prev=Count("id", filter=Q(created_at__gte=prev_since, created_at__lt=since)),
        won_now=Count("id", filter=Q(status=Opportunity.Status.WON, updated_at__gte=since)),
        lost_now=Count("id", filter=Q(status=Opportunity.Status.LOST, updated_at__gte=since)),
        qualified=Count("id", filter=Q(status__in=[Opportunity.Status.QUALIFIED, Opportunity.Status.PROPOSAL, Opportunity.Status.WON])),
    )
    won_now, lost_now = opp_agg["won_now"] or 0, opp_agg["lost_now"] or 0
    win_rate = round((won_now / (won_now + lost_now)) * 100) if (won_now + lost_now) > 0 else None

    all_opps_vals = list(Opportunity.objects.only("status", "estimated_value", "won_value", "updated_at"))
    open_vals = [o for o in all_opps_vals if o.status in Opportunity.OPEN]
    pipeline_value = sum((o.effective_value for o in open_vals), Decimal("0.00"))
    weighted_pipeline_value = sum((o.weighted_value for o in open_vals), Decimal("0.00"))
    won_value_period = sum(
        (o.effective_value for o in all_opps_vals if o.status == Opportunity.Status.WON and o.updated_at >= since),
        Decimal("0.00"),
    )
    won_value_total = sum(
        (o.effective_value for o in all_opps_vals if o.status == Opportunity.Status.WON),
        Decimal("0.00"),
    )
    stage_values: dict[str, Decimal] = {}
    for o in all_opps_vals:
        stage_values[o.status] = stage_values.get(o.status, Decimal("0.00")) + o.effective_value

    today_date_local = timezone.localdate(now)
    pay_agg = PaymentMilestone.objects.aggregate(
        collected=Sum("amount", filter=Q(status=PaymentMilestone.Status.PAID)),
        pending=Sum("amount", filter=Q(status__in=(PaymentMilestone.Status.PENDING, PaymentMilestone.Status.INVOICED))),
        overdue=Sum(
            "amount",
            filter=Q(
                status__in=(PaymentMilestone.Status.PENDING, PaymentMilestone.Status.INVOICED),
                due_date__lt=today_date_local,
            ),
        ),
    )

    # Local-timezone daily trend (last min(days, 14) days)
    span = min(days, 14)
    today_date = timezone.localdate(now)
    start_day = today_date - timedelta(days=span - 1)
    trend_start = datetime.combine(start_day, time.min, tzinfo=tz)
    day_dates = [start_day + timedelta(days=i) for i in range(span)]

    def _daily_counts(model) -> dict:
        return dict(
            model.objects.filter(created_at__gte=trend_start)
            .annotate(day=TruncDate("created_at", tzinfo=tz))
            .values_list("day")
            .annotate(c=Count("id"))
        )

    cust_day, opp_day, act_day = _daily_counts(Customer), _daily_counts(Opportunity), _daily_counts(Activity)
    daily_trend = {
        "labels": [d.strftime("%d %b") for d in day_dates],
        "customers": [cust_day.get(d, 0) for d in day_dates],
        "opportunities": [opp_day.get(d, 0) for d in day_dates],
        "activities": [act_day.get(d, 0) for d in day_dates],
    }

    total_contacts = contact_agg["total"] or 0
    total_customers = cust_agg["total"] or 0
    total_opps = opp_agg["total"] or 0
    qualified_opps = opp_agg["qualified"] or 0
    won_opps = by_status.get(Opportunity.Status.WON, 0)
    funnel_max = max(total_contacts, total_customers, total_opps, 1)
    funnel = [
        {"label": "Inbox Contacts (IG / WA)", "count": total_contacts, "pct": round(total_contacts / funnel_max * 100), "color": "bg-blue-500"},
        {"label": "CRM Customers", "count": total_customers, "pct": round(total_customers / funnel_max * 100), "color": "bg-indigo-500"},
        {"label": "Opportunities", "count": total_opps, "pct": round(total_opps / funnel_max * 100), "color": "bg-violet-500"},
        {"label": "Qualified / Proposal", "count": qualified_opps, "pct": round(qualified_opps / funnel_max * 100), "color": "bg-amber-500"},
        {"label": "Won Deals", "count": won_opps, "pct": round(won_opps / funnel_max * 100), "color": "bg-emerald-500"},
    ]

    # Source attribution in 2 grouped queries instead of 3*N queries
    cust_by_src = dict(Customer.objects.values_list("source").annotate(c=Count("id")))
    opp_by_src = {
        r["source"]: r
        for r in Opportunity.objects.values("source").annotate(
            total=Count("id"),
            won=Count("id", filter=Q(status=Opportunity.Status.WON)),
        )
    }
    source_rows = []
    for src_val, src_lbl in Source.choices:
        c_cnt = cust_by_src.get(src_val, 0)
        o_row = opp_by_src.get(src_val, {})
        o_cnt = o_row.get("total", 0)
        if c_cnt or o_cnt:
            won_cnt = o_row.get("won", 0)
            source_rows.append({
                "label": src_lbl,
                "customers": c_cnt,
                "opportunities": o_cnt,
                "won": won_cnt,
                "win_pct": round((won_cnt / o_cnt) * 100) if o_cnt else 0,
            })

    # Team leaderboard in 3 grouped queries instead of 4*N queries
    users = list(get_user_model().objects.filter(is_active=True)[:8])
    user_ids = [u.pk for u in users]
    act_by_u = dict(Activity.objects.filter(created_by_id__in=user_ids, created_at__gte=since).values_list("created_by_id").annotate(c=Count("id")))
    fu_by_u = dict(FollowUp.objects.filter(customer__isnull=False, assigned_to_id__in=user_ids, status=FollowUp.Status.DONE).values_list("assigned_to_id").annotate(c=Count("id")))
    opp_by_u = {
        r["assigned_to_id"]: r
        for r in Opportunity.objects.filter(assigned_to_id__in=user_ids).values("assigned_to_id").annotate(
            total=Count("id"),
            won=Count("id", filter=Q(status=Opportunity.Status.WON)),
        )
    }
    team_board = []
    for u in users:
        acts = act_by_u.get(u.pk, 0)
        fu_done = fu_by_u.get(u.pk, 0)
        o_row = opp_by_u.get(u.pk, {})
        opps_u = o_row.get("total", 0)
        if acts or fu_done or opps_u or u == user:
            team_board.append({
                "name": u.get_full_name() or u.username,
                "is_you": u == user,
                "activities": acts,
                "followups_done": fu_done,
                "opportunities": opps_u,
                "won": o_row.get("won", 0),
            })

    return {
        "days": days,
        "ranges": [7, 30, 90],
        "stats": {
            "open": opp_agg["open_cnt"] or 0,
            "new_this_week": opp_agg["new_week"] or 0,
            "won_this_month": opp_agg["won_month"] or 0,
            "customers": total_customers,
        },
        "activity_kpis": [
            {"label": "Calls", "value": act_agg["calls_now"] or 0, "delta": _delta(act_agg["calls_now"] or 0, act_agg["calls_prev"] or 0), "dot": "bg-blue-500", "sub": "logged in period"},
            {"label": "Meetings", "value": meeting_agg["now"] or 0, "delta": _delta(meeting_agg["now"] or 0, meeting_agg["prev"] or 0), "dot": "bg-violet-500", "sub": f"{meeting_agg['upcoming'] or 0} upcoming"},
            {"label": "Follow-ups", "value": fu_pending, "delta": None, "dot": "bg-amber-500", "sub": f"{fu_overdue} overdue · {fu_pending - fu_overdue} upcoming", "overdue": fu_overdue},
            {"label": "Notes", "value": act_agg["notes_now"] or 0, "delta": _delta(act_agg["notes_now"] or 0, act_agg["notes_prev"] or 0), "dot": "bg-emerald-500", "sub": "added in period"},
        ],
        "pipeline_kpis": [
            {"label": "Contacts (IG/WA)", "value": contact_agg["now"] or 0, "delta": _delta(contact_agg["now"] or 0, contact_agg["prev"] or 0), "dot": "bg-blue-500"},
            {"label": "New Customers", "value": cust_agg["now"] or 0, "delta": _delta(cust_agg["now"] or 0, cust_agg["prev"] or 0), "dot": "bg-indigo-500"},
            {"label": "Opportunities", "value": opp_agg["now"] or 0, "delta": _delta(opp_agg["now"] or 0, opp_agg["prev"] or 0), "dot": "bg-violet-500"},
            {"label": "Win Rate", "value": f"{win_rate}%" if win_rate is not None else "—", "delta": None, "dot": "bg-emerald-500", "sub": f"{won_now} won · {lost_now} lost"},
        ],
        "daily_trend": daily_trend,
        "funnel": funnel,
        "source_rows": source_rows,
        "team_board": team_board,
        "do_next_followups": FollowUp.objects.filter(customer__isnull=False, status=FollowUp.Status.PENDING).select_related("customer", "opportunity", "channel").order_by("due_at")[:6],
        "pipeline": [(label, by_status.get(value, 0), value, stage_values.get(value, Decimal("0.00"))) for value, label in Opportunity.Status.choices],
        "pipeline_total": max(total_opps, 1),
        "revenue": {
            "pipeline_value": pipeline_value,
            "weighted_value": weighted_pipeline_value,
            "won_period": won_value_period,
            "won_total": won_value_total,
            "collected": pay_agg["collected"] or Decimal("0.00"),
            "pending_pay": pay_agg["pending"] or Decimal("0.00"),
            "overdue_pay": pay_agg["overdue"] or Decimal("0.00"),
        },
        "stale_opps": opportunities.filter(status__in=Opportunity.OPEN, updated_at__lt=now - timedelta(days=7)).count(),
        "pending_approvals": PendingAIReply.objects.filter(status=PendingAIReply.STATUS_PENDING).count(),
        "recent": opportunities[:8],
        "follow_ups": opportunities.filter(status__in=Opportunity.OPEN, next_follow_up__lte=now + timedelta(days=1)).order_by("next_follow_up")[:8],
        "meetings": Meeting.objects.filter(status=Meeting.Status.SCHEDULED, start_at__gte=now - timedelta(hours=1)).select_related("customer")[:6],
        "waiting_chats": Thread.objects.filter(needs_human=True).order_by("-updated_at")[:5],
    }
