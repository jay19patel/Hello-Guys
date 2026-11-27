from datetime import datetime, time, timedelta
from django.db.models import Count, Q
from django.db.models.functions import ExtractHour, ExtractIsoWeekDay, TruncDate
from django.utils import timezone
from meta_connector.models import AutoDMLog, Contact, Message, PlatformAccount

RANGES = (7, 30, 90)
WEEKDAYS = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
INCOMING_DM = Q(direction=Message.DIRECTION_INCOMING) & ~Q(message_type=Message.TYPE_COMMENT)


def _message_kpis(qs):
    return qs.aggregate(
        total=Count('id'),
        received=Count('id', filter=INCOMING_DM),
        sent=Count('id', filter=Q(direction=Message.DIRECTION_OUTGOING)),
        comments=Count('id', filter=Q(message_type=Message.TYPE_COMMENT)),
    )


def _delta(now, before):
    """% change vs the previous period; None when there is nothing to compare with."""
    return round((now - before) * 100 / before) if before else None


def _pct(part, whole):
    return round(part * 100 / whole) if whole else 0


def parse_days(request) -> int:
    return int(request.GET['days']) if request.GET.get('days') in {str(r) for r in RANGES} else 30


def insights_context(days: int) -> dict:
    """Numbers, charts data and takeaways for the last `days` days (used by the Overview page)."""
    tz = timezone.get_current_timezone()
    today = timezone.localdate()
    first_day = today - timedelta(days=days - 1)
    start = datetime.combine(first_day, time.min, tzinfo=tz)
    prev_start = start - timedelta(days=days)

    period = Message.objects.filter(created_at__gte=start)
    previous = Message.objects.filter(created_at__gte=prev_start, created_at__lt=start)
    cur, before = _message_kpis(period), _message_kpis(previous)
    new_contacts = Contact.objects.filter(first_seen__gte=start).count()
    prev_new_contacts = Contact.objects.filter(first_seen__gte=prev_start, first_seen__lt=start).count()
    dm = dict(AutoDMLog.objects.filter(created_at__gte=start).values_list('status').annotate(n=Count('id')))
    prev_dm_sent = AutoDMLog.objects.filter(created_at__gte=prev_start, created_at__lt=start, status=AutoDMLog.STATUS_SENT).count()

    kpis = [
        {'label': 'Messages', 'icon': 'fa-regular fa-comments', 'value': cur['total'], 'delta': _delta(cur['total'], before['total'])},
        {'label': 'Received DMs', 'icon': 'fa-solid fa-arrow-down', 'value': cur['received'], 'delta': _delta(cur['received'], before['received'])},
        {'label': 'Replies sent', 'icon': 'fa-solid fa-arrow-up', 'value': cur['sent'], 'delta': _delta(cur['sent'], before['sent'])},
        {'label': 'Comments', 'icon': 'fa-regular fa-comment', 'value': cur['comments'], 'delta': _delta(cur['comments'], before['comments'])},
        {'label': 'New contacts', 'icon': 'fa-solid fa-user-plus', 'value': new_contacts, 'delta': _delta(new_contacts, prev_new_contacts)},
        {'label': 'Auto-DMs sent', 'icon': 'fa-regular fa-paper-plane', 'value': dm.get('sent', 0), 'delta': _delta(dm.get('sent', 0), prev_dm_sent)},
    ]

    for k in kpis:
        k['abs_delta'] = abs(k['delta']) if k['delta'] is not None else None

    # Daily messages per channel (every day present, zeros included).
    day_list = [first_day + timedelta(days=i) for i in range(days)]
    per_day = {(r['day'], r['platform_account__platform']): r['n'] for r in
               period.annotate(day=TruncDate('created_at', tzinfo=tz)).values('day', 'platform_account__platform').annotate(n=Count('id'))}
    daily = {
        'days': [d.isoformat() for d in day_list],
        'instagram': [per_day.get((d, 'instagram'), 0) for d in day_list],
        'whatsapp': [per_day.get((d, 'whatsapp'), 0) for d in day_list],
    }
    totals_by_channel = {'instagram': sum(daily['instagram']), 'whatsapp': sum(daily['whatsapp'])}

    # Per account.
    in_period = Q(messages__created_at__gte=start)
    accounts = list(PlatformAccount.objects.annotate(
        received=Count('messages', filter=in_period & Q(messages__direction=Message.DIRECTION_INCOMING) & ~Q(messages__message_type=Message.TYPE_COMMENT)),
        sent=Count('messages', filter=in_period & Q(messages__direction=Message.DIRECTION_OUTGOING)),
        comments=Count('messages', filter=in_period & Q(messages__message_type=Message.TYPE_COMMENT)),
        people=Count('messages__contact', filter=in_period, distinct=True),
    ))
    for acc in accounts:
        acc.total = acc.received + acc.sent + acc.comments
    accounts.sort(key=lambda a: -a.total)
    max_account = max([a.total for a in accounts] or [0])

    # When contacts reach out: incoming DMs + comments by weekday x hour (local time).
    grid = [[0] * 24 for _ in range(7)]
    for r in (period.filter(direction=Message.DIRECTION_INCOMING)
              .annotate(wd=ExtractIsoWeekDay('created_at', tzinfo=tz), h=ExtractHour('created_at', tzinfo=tz))
              .values('wd', 'h').annotate(n=Count('id'))):
        grid[r['wd'] - 1][r['h']] = r['n']

    top_contacts = (Contact.objects.select_related('platform_account')
                     .annotate(n=Count('messages', filter=Q(messages__created_at__gte=start)))
                     .filter(n__gt=0).order_by('-n')[:5])

    # Replies per team member (auto-DMs and phone-app replies have no user).
    team_replies = []
    for r in (period.filter(direction=Message.DIRECTION_OUTGOING)
              .values('sent_by__username', 'sent_by__first_name', 'sent_by__last_name').annotate(n=Count('id')).order_by('-n')):
        name = ' '.join(filter(None, [r['sent_by__first_name'], r['sent_by__last_name']])) or r['sent_by__username']
        team_replies.append({'name': name or 'Automated / phone app', 'is_person': bool(r['sent_by__username']), 'n': r['n']})
    max_replies = max([t['n'] for t in team_replies] or [0])

    dm_attempts = dm.get('sent', 0) + dm.get('failed', 0)
    autodm = {'sent': dm.get('sent', 0), 'failed': dm.get('failed', 0), 'skipped': dm.get('skipped', 0),
              'success_rate': _pct(dm.get('sent', 0), dm_attempts) if dm_attempts else None}

    return {
        'days': days, 'ranges': RANGES, 'tz_name': timezone.get_current_timezone_name(), 'first_day': first_day, 'today': today,
        'kpis': kpis, 'daily': daily, 'totals_by_channel': totals_by_channel,
        'accounts': accounts, 'max_account': max_account,
        'heatmap': {'grid': grid, 'weekdays': WEEKDAYS},
        'top_contacts': top_contacts, 'autodm': autodm,
        'team_replies': team_replies, 'max_replies': max_replies,
        'insights': _insights(days, cur, before, daily, totals_by_channel, grid, accounts, autodm),
    }


def _hour_label(h: int) -> str:
    return f"{h % 12 or 12} {'AM' if h < 12 else 'PM'}"


def _insights(days, cur, before, daily, by_channel, grid, accounts, autodm):
    """Plain-language takeaways, most useful first. Each item: (icon, text)."""
    out = []
    total = cur['total']
    if not total:
        return [('fa-regular fa-moon', f"No messages in the last {days} days yet. Insights appear as soon as people message or comment.")]

    change = _delta(total, before['total'])
    if change is not None:
        word = 'up' if change >= 0 else 'down'
        out.append(('fa-solid fa-arrow-trend-up' if change >= 0 else 'fa-solid fa-arrow-trend-down',
                    f"Messages are {word} {abs(change)}% compared with the previous {days} days ({before['total']} → {total})."))

    leader = max(by_channel, key=by_channel.get)
    if by_channel[leader]:
        out.append(('fa-brands fa-instagram' if leader == 'instagram' else 'fa-brands fa-whatsapp',
                    f"{leader.title()} brings {_pct(by_channel[leader], total)}% of your messages."))

    day_totals = [i + w for i, w in zip(daily['instagram'], daily['whatsapp'])]
    best = max(range(len(day_totals)), key=day_totals.__getitem__)
    best_day = datetime.fromisoformat(daily['days'][best]).strftime('%a, %d %b')
    out.append(('fa-regular fa-calendar', f"Busiest day was {best_day} with {day_totals[best]} messages."))

    hour_totals = [sum(row[h] for row in grid) for h in range(24)]
    if any(hour_totals):
        h = max(range(24), key=hour_totals.__getitem__)
        wd = max(range(7), key=lambda d: sum(grid[d]))
        out.append(('fa-regular fa-clock', f"Contacts reach out most between {_hour_label(h)} and {_hour_label((h + 1) % 24)}, "
                                           f"and most often on {['Mondays', 'Tuesdays', 'Wednesdays', 'Thursdays', 'Fridays', 'Saturdays', 'Sundays'][wd]}. Reply fast then."))

    if cur['received'] and cur['sent'] < cur['received']:
        out.append(('fa-regular fa-comment-dots', f"You sent {cur['sent']} replies for {cur['received']} received DMs. Open a contact to reply from the dashboard."))

    if autodm['success_rate'] is not None:
        out.append(('fa-regular fa-paper-plane', f"Auto-DM reached {autodm['sent']} people ({autodm['success_rate']}% delivered)."
                                                 + (f" {autodm['failed']} failed; see the account page for the reason." if autodm['failed'] else '')))

    active = [a for a in accounts if a.total]
    if len(active) > 1:
        out.append(('fa-solid fa-tower-broadcast', f"“{active[0].display_name}” handles {_pct(active[0].total, total)}% of all messages."))
    return out
