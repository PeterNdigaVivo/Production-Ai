from celery import Celery
from celery.schedules import crontab
from app.core.config import get_settings

_settings = get_settings()

celery_app = Celery(
    "production_ai",
    broker=_settings.redis_url,
    backend=_settings.redis_url,
    include=["app.workers.tasks"],
)

celery_app.conf.update(
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_default_queue="default",
    worker_prefetch_multiplier=4,
    timezone="UTC",
    enable_utc=True,
    beat_schedule={
        "rollup-production-records-every-5m": {
            "task": "app.workers.tasks.rollup_production_records",
            "schedule": crontab(minute="*/5"),
        },
        "camera-heartbeat-check-every-1m": {
            "task": "app.workers.tasks.check_camera_heartbeats",
            "schedule": crontab(minute="*"),
        },
        # Debounced idle alerting (Finding 6): periodic check replaces the old
        # per-transition spam in the event-engine.
        "idle-worker-check-every-1m": {
            "task": "app.workers.tasks.check_idle_workers",
            "schedule": crontab(minute="*"),
        },
    },
)
