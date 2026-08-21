"""
Django settings for the invoice_ai project.

This project exposes a single JSON API (see `extraction/`) that accepts a
PDF invoice/receipt and returns structured warranty tracking fields, using
local rule-based text extraction (extraction/../local_invoice_extractor.py)
— fully offline, no AI/cloud API, no API key. There are no database-backed
models — the DATABASES setting below exists only because Django requires
one to boot.
"""

import os
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")


def _env_bool(name, default=False):
    val = os.environ.get(name)
    if val is None:
        return default
    return val.strip().lower() in ("1", "true", "yes", "on")


# --- Core ---
SECRET_KEY = os.environ.get("DJANGO_SECRET_KEY", "dev-insecure-secret-key-change-me")
DEBUG = _env_bool("DJANGO_DEBUG", default=False)

_allowed_hosts = os.environ.get("DJANGO_ALLOWED_HOSTS", "*")
ALLOWED_HOSTS = [h.strip() for h in _allowed_hosts.split(",") if h.strip()]

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.staticfiles",
    "rest_framework",
    "extraction",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.middleware.common.CommonMiddleware",
]

ROOT_URLCONF = "invoice_ai.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.debug",
                "django.template.context_processors.request",
            ],
        },
    },
]

WSGI_APPLICATION = "invoice_ai.wsgi.application"
ASGI_APPLICATION = "invoice_ai.asgi.application"

# Unused by this project (no models), but required for Django to start.
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": BASE_DIR / "db.sqlite3",
    }
}

LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# --- Uploads ---
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "15"))
_max_upload_bytes = MAX_UPLOAD_MB * 1024 * 1024
DATA_UPLOAD_MAX_MEMORY_SIZE = _max_upload_bytes
FILE_UPLOAD_MAX_MEMORY_SIZE = _max_upload_bytes

# --- Django REST Framework ---
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": [],
    # No django.contrib.auth in INSTALLED_APPS (this API has no DB-backed
    # users) — disable DRF's default AnonymousUser fallback so it doesn't
    # try to resolve the auth app's Permission model.
    "UNAUTHENTICATED_USER": None,
    "DEFAULT_PARSER_CLASSES": [
        "rest_framework.parsers.MultiPartParser",
        "rest_framework.parsers.FormParser",
        "rest_framework.parsers.JSONParser",
    ],
    "DEFAULT_RENDERER_CLASSES": [
        "rest_framework.renderers.JSONRenderer",
    ],
    "EXCEPTION_HANDLER": "extraction.exceptions.custom_exception_handler",
    "DEFAULT_THROTTLE_CLASSES": ["rest_framework.throttling.AnonRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {
        "anon": os.environ.get("EXTRACTION_RATE_LIMIT", "60/min"),
    },
}

# --- Logging ---
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "standard": {
            "format": "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "standard",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
    "loggers": {
        "extraction": {
            "handlers": ["console"],
            "level": "INFO",
            "propagate": False,
        },
        "django.request": {
            "handlers": ["console"],
            "level": "WARNING",
            "propagate": False,
        },
    },
}
