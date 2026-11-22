"""Fire scheduled triggers. Run ONE long-lived process (or several: a Redis lock elects a leader).

python manage.py hello_guy_scheduler          # loop forever
python manage.py hello_guy_scheduler --once   # single tick (cron / tests)
"""

import logging
import time
import uuid

from django.core.management.base import BaseCommand

from hello_guy_ai.events import get_redis
from hello_guy_ai.triggers import due_scheduled_triggers

logger = logging.getLogger(__name__)

LOCK_KEY = "hello_guy_ai:scheduler:leader"
LOCK_TTL = 90
TICK_SECONDS = 30
# Renew only if we still own the lock (atomic compare-and-expire).
RENEW = "if redis.call('get', KEYS[1]) == ARGV[1] then return redis.call('expire', KEYS[1], ARGV[2]) else return 0 end"


class Command(BaseCommand):
    help = "Fire due scheduled triggers"

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Run one tick and exit")

    def handle(self, *args, once: bool = False, **options):
        from hello_guy_ai.tasks import fire_trigger

        redis = get_redis()
        me = uuid.uuid4().hex
        while True:
            leader = redis.set(LOCK_KEY, me, nx=True, ex=LOCK_TTL) or redis.eval(RENEW, 1, LOCK_KEY, me, LOCK_TTL)
            if leader:
                for trigger_id in due_scheduled_triggers():
                    fire_trigger.send(trigger_id)
                    logger.info("Scheduled trigger %s fired", trigger_id)
            if once:
                return
            time.sleep(TICK_SECONDS)
