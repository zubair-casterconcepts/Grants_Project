# Celery app is loaded lazily when celery workers start.
# Importing here is optional so the login app runs without Celery installed.
try:
    from .celery import app as celery_app
except ImportError:
    celery_app = None

__all__ = ("celery_app",)
