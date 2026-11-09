from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import isa9000
from django.core.exceptions import ImproperlyConfigured

from shared.payloads import MAX_ENVELOPE_BYTES

BASE_DIR = Path(__file__).resolve().parent.parent
PACKAGE_DIR = Path(isa9000.__file__).parent
DEBUG = os.environ.get("DEBUG", "false").lower() in {"1", "true"}
SECRET_KEY = os.environ["ISA9000_DJANGO_SECRET_KEY"]
AUTH_MODE = os.environ.get("ISA9000_AUTH", "easyauth")
if AUTH_MODE not in {"easyauth", "entra"}:
    raise ImproperlyConfigured("ISA9000_AUTH must be easyauth or entra.")
ENTRA_TENANT_ID = os.environ.get("ISA9000_ENTRA_TENANT_ID", "")
ENTRA_CLIENT_ID = os.environ.get("ISA9000_ENTRA_CLIENT_ID", "")
ENTRA_CLIENT_SECRET = os.environ.get("ISA9000_ENTRA_CLIENT_SECRET", "")
ENTRA_REDIRECT_URI = os.environ.get(
    "ISA9000_ENTRA_REDIRECT_URI", "http://localhost:8000/auth/login/aad/callback"
)
ALLOWED_HOSTS = os.environ.get("ISA9000_ALLOWED_HOSTS", "localhost,127.0.0.1").split(",")
ADMIN_GROUP_ID = os.environ.get("ISA9000_ADMIN_GROUP_ID", "")
GRAPH_GROUPS = AUTH_MODE == "easyauth" and os.environ.get("ISA9000_GRAPH_GROUPS") == "true"
CALLBACK_SECRET = os.environ.get("ISA9000_CALLBACK_SECRET", "")
CALLBACK_BASE_URL = os.environ.get("ISA9000_CALLBACK_BASE_URL", "http://web:8000").rstrip("/")
DAILY_RUNS_PER_USER = int(os.environ.get("ISA9000_DAILY_RUNS_PER_USER", "100"))
MAX_UPLOAD_MB = int(os.environ.get("ISA9000_MAX_UPLOAD_MB", "10"))
MAX_FILES = int(os.environ.get("ISA9000_MAX_FILES", "5"))
DATA_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
CALLBACK_MAX_BODY_SIZE = MAX_ENVELOPE_BYTES
DATA_UPLOAD_MAX_NUMBER_FILES = MAX_FILES
FILE_UPLOAD_HANDLERS = [
    "core.uploads.LimitedUploadHandler",
    "django.core.files.uploadhandler.MemoryFileUploadHandler",
    "django.core.files.uploadhandler.TemporaryFileUploadHandler",
]

INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "app_portal",
]
MIDDLEWARE = [
    "core.middleware.RequestMiddleware",
    "core.middleware.ErrorMiddleware",
    "core.uploads.BodyLimitMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "core.middleware.IdentityMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "core.urls"
WSGI_APPLICATION = "core.wsgi.application"
ASGI_APPLICATION = "core.asgi.application"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ]
        },
    },
    {
        "BACKEND": "django.template.backends.jinja2.Jinja2",
        "DIRS": [BASE_DIR / "app_portal" / "jinja2"],
        "OPTIONS": {"environment": "core.branding.environment"},
    },
]
database = urlsplit(os.environ["DATABASE_URL"])
if database.scheme == "sqlite":
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": ":memory:" if database.path == "/:memory:" else database.path,
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": unquote(database.path.lstrip("/")),
            "USER": unquote(database.username or ""),
            "PASSWORD": unquote(database.password or ""),
            "HOST": database.hostname,
            "PORT": database.port or 5432,
            "CONN_MAX_AGE": 60,
            "OPTIONS": {key: values[-1] for key, values in parse_qs(database.query).items()},
        }
    }
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
USE_TZ = True
TIME_ZONE = "UTC"
LANGUAGE_CODE = "en-us"
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [("isa9000", PACKAGE_DIR / "presentation_website_static")]
STATICFILES_FINDERS = [
    "django.contrib.staticfiles.finders.FileSystemFinder",
    "django.contrib.staticfiles.finders.AppDirectoriesFinder",
    "core.staticfiles.LogoFinder",
]
WHITENOISE_AUTOREFRESH = DEBUG or AUTH_MODE == "entra"
WHITENOISE_USE_FINDERS = DEBUG or AUTH_MODE == "entra"
LOCAL_ENTRA = AUTH_MODE == "entra"
LOCAL_TEST_USERS = os.environ.get("ISA9000_LOCAL_TEST_USERS", "false").lower() == "true"
if LOCAL_TEST_USERS and not LOCAL_ENTRA:
    raise ImproperlyConfigured("Local test sessions require local Entra authentication.")
if LOCAL_ENTRA and any(host not in {"localhost", "127.0.0.1", "web"} for host in ALLOWED_HOSTS):
    raise ImproperlyConfigured("Local Entra authentication requires loopback hosts.")
SECURE_SSL_REDIRECT = not DEBUG and not LOCAL_ENTRA
SECURE_REDIRECT_EXEMPT = [r"^healthz$"]
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
SESSION_COOKIE_SECURE = not DEBUG and not LOCAL_ENTRA
SESSION_IDLE_TIMEOUT = 2 * 60 * 60
SESSION_MAX_AGE = 8 * 60 * 60
SESSION_COOKIE_AGE = SESSION_MAX_AGE
CSRF_COOKIE_SECURE = not DEBUG and not LOCAL_ENTRA
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
CSRF_TRUSTED_ORIGINS = [f"https://{host}" for host in ALLOWED_HOSTS if host != "*"]
CSRF_FAILURE_VIEW = "core.views.csrf_failure"
LOGIN_URL = "/.auth/login/aad?post_login_redirect_uri=/libraries/"
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "loggers": {
        "django.request": {"handlers": [], "propagate": False},
        "django.security": {"handlers": [], "propagate": False},
    },
}
