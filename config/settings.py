"""
Single settings module on purpose: everything runs locally, so the few values
that vary (Redis URL, rate-limit knobs) come from environment variables with
sensible defaults rather than a settings package.
"""
import os
import sys
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

# Local assessment project only — never reuse this pattern in production.
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "local-dev-only-not-a-real-secret")

DEBUG = os.environ.get("DJANGO_DEBUG", "1") == "1"

ALLOWED_HOSTS = ["*"]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "orders",
    "emailer",
    "tenants",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "tenants.middleware.TenantMiddleware",
]

# django-silk stays out of the test run: pytest-django flips settings.DEBUG
# to False only *after* this module is imported, so gate on the test runner
# itself. Otherwise silk's bookkeeping INSERTs would pollute every
# assertNumQueries in the suite.
TESTING = "pytest" in sys.modules or "test" in sys.argv

if DEBUG and not TESTING:
    INSTALLED_APPS.append("silk")
    MIDDLEWARE.insert(0, "silk.middleware.SilkyMiddleware")

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
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

WSGI_APPLICATION = "config.wsgi.application"

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

AUTH_PASSWORD_VALIDATORS = []

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# ---------------------------------------------------------------------------
# Redis / Celery (Section 2)
# ---------------------------------------------------------------------------

REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_TASK_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]

# Crash safety: ack only after the task finishes, and requeue the message if
# the worker process dies mid-execution (e.g. SIGKILL / OOM). Together these
# give at-least-once delivery — see ANSWERS.md section 2.
CELERY_TASK_ACKS_LATE = True
CELERY_TASK_REJECT_ON_WORKER_LOST = True

# Don't let one worker hoard messages it hasn't started; with acks_late a
# prefetched-but-unstarted message would be locked to a dead worker until the
# visibility timeout expired.
CELERY_WORKER_PREFETCH_MULTIPLIER = 1

# How long Redis waits before re-delivering an unacked message from a worker
# it considers dead. Must comfortably exceed the longest legitimate task run.
CELERY_BROKER_TRANSPORT_OPTIONS = {"visibility_timeout": 3600}

# ---------------------------------------------------------------------------
# Email queue knobs (Section 2) — overridable so tests can shrink the window
# ---------------------------------------------------------------------------

EMAIL_RATE_LIMIT = int(os.environ.get("EMAIL_RATE_LIMIT", "200"))
EMAIL_RATE_WINDOW_SECONDS = float(os.environ.get("EMAIL_RATE_WINDOW_SECONDS", "60"))
EMAIL_MAX_SEND_ATTEMPTS = int(os.environ.get("EMAIL_MAX_SEND_ATTEMPTS", "5"))
EMAIL_RETRY_BACKOFF_BASE_SECONDS = float(os.environ.get("EMAIL_RETRY_BACKOFF_BASE_SECONDS", "2"))
EMAIL_RETRY_BACKOFF_MAX_SECONDS = float(os.environ.get("EMAIL_RETRY_BACKOFF_MAX_SECONDS", "300"))

# Dotted path so the provider is swappable. The default "flaky" provider
# succeeds for every normal message and fails on the magic `fail-twice-*` /
# `fail-always-*` ids, which is what lets the tests and the live demo
# exercise the retry and dead-letter paths without any extra wiring.
EMAIL_PROVIDER = os.environ.get("EMAIL_PROVIDER", "emailer.providers.FlakyEmailProvider")
