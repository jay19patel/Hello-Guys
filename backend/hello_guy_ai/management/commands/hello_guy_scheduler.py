"""Scheduler: due reminders, scheduled triggers, stale-run cleanup, daily knowledge reconcile.

Run ONE long-lived process (or several: a Redis lock elects a leader; reminders are exactly-once anyway).

python manage.py hello_guy_scheduler          # loop forever
python manage.py hello_guy_scheduler --once   # single tick (cron / tests)
"""

import logging
import time
import uuid

from django.core.management.base import BaseCommand

from hello_guy_ai.core.events import get_redis
from hello_guy_ai.services.chat import fail_stale_runs
from hello_guy_ai.services.reminders import fire_due_reminders
from hello_guy_ai.services.triggers import due_scheduled_triggers

logger = logging.getLogger(__name__)

LOCK_KEY = "hello_guy_ai:scheduler:leader"
RECONCILE_KEY = "hello_guy_ai:scheduler:knowledge-reconcile"
LOCK_TTL = 90
TICK_SECONDS = 15  # a reminder fires at most this late
# Renew only if we still own the lock (atomic compare-and-expire).
RENEW = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end"


class Command(BaseCommand):
    help = "Fire due reminders and scheduled triggers, clean up stale runs, reconcile knowledge daily"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run one tick and exit")

    def handle(self, *args, once: bool = False, **options):
        from hello_guy_ai.tasks.actors import fire_trigger, reconcile_knowledge

        redis = get_redis()
        me = uuid.uuid4().hex
        while True:
            leader = redis.set(LOCK_KEY, me, nx=True, ex=LOCK_TTL) or redis.eval(RENEW, 1, LOCK_KEY, me, LOCK_TTL)
            if leader:
                try:
                    self.tick(redis, fire_trigger, reconcile_knowledge)
                except Exception:  # DB/broker blip: log and try again next tick, never exit
                    logger.exception("Scheduler tick failed")
            if once:
                return
            time.sleep(TICK_SECONDS)

    def tick(self, redis, fire_trigger, reconcile_knowledge) -> None:
        if fired := fire_due_reminders():
            logger.info("Fired %s reminder(s)", fired)
        for trigger_id in due_scheduled_triggers():
            fire_trigger.send(trigger_id)
            logger.info("Scheduled trigger %s fired", trigger_id)
        fail_stale_runs()
        if redis.set(RECONCILE_KEY, "1", nx=True, ex=24 * 3600):  # first tick after deploy, then daily
            reconcile_knowledge.send()
