import os

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "grants.settings")

try:
    from celery import Celery
except ImportError:  # Celery is optional for the login-only setup
    app = None
else:
    app = Celery("grants")
    app.config_from_object("django.conf:settings", namespace="CELERY")
    app.autodiscover_tasks()
