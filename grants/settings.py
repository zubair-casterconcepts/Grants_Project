"""
Django settings for the Grants project.
Database: Supabase Postgres only (DATABASE_URL required).
"""
import os
import socket
import time
from pathlib import Path

import dj_database_url
from django.core.exceptions import ImproperlyConfigured
from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.getenv("SECRET_KEY", "django-insecure-dev-only-change-me")
DEBUG = os.getenv("DEBUG", "True").lower() in ("1", "true", "yes")
ALLOWED_HOSTS = [
    host.strip()
    for host in os.getenv("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
    if host.strip()
]


def _resolve_ipv4(hostname: str, attempts: int = 3) -> str:
    """Resolve A record; retries help flaky local DNS resolvers."""
    last_error = None
    for attempt in range(attempts):
        try:
            infos = socket.getaddrinfo(hostname, 5432, socket.AF_INET, socket.SOCK_STREAM)
            if infos:
                return infos[0][4][0]
        except OSError as exc:
            last_error = exc
            time.sleep(0.4 * (attempt + 1))
    if last_error:
        raise last_error
    raise OSError(f"No IPv4 address found for {hostname}")


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "apps.accounts.apps.AccountsConfig",
    "apps.auth.apps.AuthConfig",
    "apps.projects.apps.ProjectsConfig",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "apps.accounts.middleware.OnboardingMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "grants.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

WSGI_APPLICATION = "grants.wsgi.application"

# Supabase Postgres only — no SQLite fallback.
DATABASE_URL = os.getenv("DATABASE_URL", "").strip()
if not DATABASE_URL:
    raise ImproperlyConfigured(
        "DATABASE_URL is required. Set it in .env to your Supabase Postgres URI."
    )

DATABASES = {
    "default": dj_database_url.parse(
        DATABASE_URL,
        conn_max_age=60,
        ssl_require=True,
    )
}

# Local DNS (ISP routers) often fails for *.pooler.supabase.com.
# Keep HOST for TLS SNI, pin IPv4 via hostaddr so psycopg2 can connect.
_db_host = DATABASES["default"].get("HOST") or ""
_db_hostaddr = os.getenv("DATABASE_HOSTADDR", "").strip()
if not _db_hostaddr and _db_host:
    try:
        _db_hostaddr = _resolve_ipv4(_db_host)
    except OSError:
        _db_hostaddr = ""

if _db_hostaddr:
    _options = DATABASES["default"].setdefault("OPTIONS", {})
    _options["hostaddr"] = _db_hostaddr
    _options.setdefault("sslmode", "require")
elif _db_host:
    raise ImproperlyConfigured(
        f"Could not resolve database host '{_db_host}'. "
        "Set DATABASE_HOSTADDR in .env to a pooler IPv4 "
        "(nslookup aws-0-ap-southeast-1.pooler.supabase.com 8.8.8.8), "
        "or switch your PC DNS to 8.8.8.8 / 1.1.1.1."
    )


AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "/static/"
STATICFILES_DIRS = [BASE_DIR / "static"]
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {
        "BACKEND": "django.core.files.storage.FileSystemStorage",
    },
    "staticfiles": {
        "BACKEND": (
            "django.contrib.staticfiles.storage.StaticFilesStorage"
            if DEBUG
            else "whitenoise.storage.CompressedManifestStaticFilesStorage"
        ),
    },
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Custom user → Postgres table: grant_user
AUTH_USER_MODEL = "accounts.GrantUser"

LOGIN_URL = "auth:login"
LOGIN_REDIRECT_URL = "auth:home"
LOGOUT_REDIRECT_URL = "auth:login"

# Supabase API (REST / Auth). Django ORM still uses DATABASE_URL (Postgres).
SUPABASE_URL = os.getenv("NEXT_PUBLIC_SUPABASE_URL", os.getenv("SUPABASE_URL", "")).rstrip("/")
SUPABASE_ANON_KEY = os.getenv("NEXT_PUBLIC_SUPABASE_ANON_KEY", os.getenv("SUPABASE_ANON_KEY", ""))
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

# OpenAI Agents SDK — used by services/grant_agent.py
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "").strip()
OPENAI_MODEL = os.getenv("OPENAI_MODEL", "gpt-5.5").strip() or "gpt-5.5"
if OPENAI_API_KEY:
    os.environ["OPENAI_API_KEY"] = OPENAI_API_KEY
os.environ["OPENAI_MODEL"] = OPENAI_MODEL

# GrantedAI Data API — used by services/granted_ai.py (anonymous key used if blank)
GRANTED_API_KEY = os.getenv("GRANTED_API_KEY", "").strip()
if GRANTED_API_KEY:
    os.environ["GRANTED_API_KEY"] = GRANTED_API_KEY

CELERY_BROKER_URL = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.getenv("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)
CELERY_TIMEZONE = os.getenv("CELERY_TIMEZONE", "UTC")
CELERY_ENABLE_UTC = True
# Monday 09:00 UTC — builds digests and POSTs each user's payload to n8n.
# Requires Redis + `celery -A grants worker` + `celery -A grants beat`.
try:
    from celery.schedules import crontab
except ImportError:  # Celery not installed in this environment
    CELERY_BEAT_SCHEDULE = {}
else:
    _digest_hour = int(os.getenv("WEEKLY_DIGEST_HOUR_UTC", "9"))
    _digest_minute = int(os.getenv("WEEKLY_DIGEST_MINUTE_UTC", "0"))
    CELERY_BEAT_SCHEDULE = {
        "weekly-grant-digest-monday": {
            "task": "accounts.send_weekly_digests",
            "schedule": crontab(
                minute=_digest_minute,
                hour=_digest_hour,
                day_of_week=1,  # Monday
            ),
        },
    }

# Weekly grant digest → n8n webhook (n8n owns the actual email delivery).
N8N_WEEKLY_WEBHOOK_URL = os.getenv("N8N_WEEKLY_GRANTED_WEBHOOK_URL", "").strip()
# Header name must match the n8n "Header Auth" credential on the webhook node.
N8N_WEBHOOK_AUTH_HEADER = (
    os.getenv("N8N_WEBHOOK_AUTH_HEADER", "").strip() or "X-Webhook-Token"
)
N8N_WEBHOOK_AUTH_HEADER_VALUE = os.getenv("N8N_WEBHOOK_AUTH_HEADER_VALUE", "").strip()
N8N_WEBHOOK_TIMEOUT_SECONDS = float(os.getenv("N8N_WEBHOOK_TIMEOUT_SECONDS", "60"))

# Digest content/branding. SITE_BASE_URL builds absolute links inside the email.
SITE_BASE_URL = os.getenv("SITE_BASE_URL", "http://localhost:8000").rstrip("/")
SITE_NAME = os.getenv("SITE_NAME", "Grants").strip() or "Grants"
# 0 = include every ranked match the matcher returns (no email-side trim).
WEEKLY_DIGEST_MAX_GRANTS = int(os.getenv("WEEKLY_DIGEST_MAX_GRANTS", "0"))
# When MAX_GRANTS is 0, ask the matcher for up to this many ranked opportunities.
WEEKLY_DIGEST_MATCHER_LIMIT = int(os.getenv("WEEKLY_DIGEST_MATCHER_LIMIT", "50"))
WEEKLY_DIGEST_REPLY_TO = os.getenv("WEEKLY_DIGEST_REPLY_TO", "").strip()
