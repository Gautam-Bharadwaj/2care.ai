"""Celery app + beat schedule.

The reminder sweep fires daily at **9:00 AM IST** and walks every
scheduled appointment ~24h out, creating a CampaignJob per appointment
that the worker picks up at the right ETA.

We run with `timezone="Asia/Kolkata"` so the crontab actually means 9am
local — Celery's default of UTC would shift the sweep 5h30m off and call
patients at 3:30am IST, which would be bad.
"""
from celery import Celery
from celery.schedules import crontab

from config import get_settings

_settings = get_settings()

celery_app = Celery(
    "twocare",
    broker=_settings.redis_url,
    backend=_settings.redis_url,
    include=["workers.outbound"],
)
celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # IST so the 9am sweep means 9am local. Tasks store timestamps in UTC
    # regardless; this only affects beat schedules.
    timezone="Asia/Kolkata",
    enable_utc=False,
    beat_schedule={
        "daily-reminder-sweep-9am-ist": {
            "task": "twocare.sweep_reminder_window",
            "schedule": crontab(hour=9, minute=0),
            "options": {
                # Avoid backing up if the worker was offline overnight — a
                # stale sweep doesn't catch anything new.
                "expires": 60 * 60,
            },
        },
    },
)


def main() -> None:
    celery_app.worker_main(["worker", "--loglevel=info"])


def beat() -> None:
    celery_app.start(["beat", "--loglevel=info"])


if __name__ == "__main__":
    main()
