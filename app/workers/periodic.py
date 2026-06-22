from __future__ import annotations

"""
Celery Beat periodic schedule.

Start the Beat scheduler alongside a worker:
    celery -A app.workers.periodic beat --loglevel=info
Or combined:
    celery -A app.workers.periodic worker --beat --loglevel=info
"""

from celery.schedules import crontab

from app.workers.jobs import celery_app  # re-use the same app instance

celery_app.conf.beat_schedule = {
    # Pre-cache the daily report every night at 00:05 UTC
    "generate-daily-report-nightly": {
        "task": "jobs.generate_daily_report",
        "schedule": crontab(hour=0, minute=5),
    },
    # Rebuild pgvector index weekly (Sunday 02:00 UTC)
    "rebuild-vector-index-weekly": {
        "task": "jobs.rebuild_vector_index",
        "schedule": crontab(hour=2, minute=0, day_of_week="sunday"),
    },
    # Sync CRM contacts every 6 hours
    "sync-crm-contacts-6h": {
        "task": "jobs.sync_crm_contacts",
        "schedule": crontab(minute=0, hour="*/6"),
        "args": ("all",),
    },
    # Trim Redis audit log weekly (Sunday 03:00 UTC)
    "cleanup-audit-logs-weekly": {
        "task": "jobs.cleanup_audit_logs",
        "schedule": crontab(hour=3, minute=0, day_of_week="sunday"),
        "args": (5000,),
    },
}

celery_app.conf.timezone = "UTC"
