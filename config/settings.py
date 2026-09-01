"""
Django settings for the DumaFund platform.
"""

import os
import sys
from datetime import timedelta
from pathlib import Path

# -----------------------------------------------------------------------------
# Paths & environment
# -----------------------------------------------------------------------------
BASE_DIR = Path(__file__).resolve().parent.parent

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:

    def load_dotenv(*args, **kwargs):
        return False


load_dotenv(BASE_DIR.parent / ".env")
load_dotenv(BASE_DIR / ".env", override=True)


def env(key, default=None):
    return os.getenv(key, default)


def env_bool(key, default=False):
    return str(os.getenv(key, default)).lower() in {"1", "true", "yes", "on"}


def env_list(key, default=""):
    raw = os.getenv(key, default)
    return [item.strip() for item in raw.split(",") if item.strip()]


# -----------------------------------------------------------------------------
# Core security
# -----------------------------------------------------------------------------
SECRET_KEY = env("DJANGO_SECRET_KEY", "dev-insecure-change-me")
DEBUG = env_bool("DJANGO_DEBUG", True)

# -----------------------------------------------------------------------------
# Allowed hosts and CSRF trusted origins
# -----------------------------------------------------------------------------
ALLOWED_HOSTS = env_list("DJANGO_ALLOWED_HOSTS", "localhost,127.0.0.1")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS", "http://localhost:5173")

# -----------------------------------------------------------------------------
# Installed apps
# -----------------------------------------------------------------------------
INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.sites",
    # third-party
    "rest_framework",
    "rest_framework_simplejwt",
    "corsheaders",
    "django_filters",
    "drf_spectacular",
    "allauth",
    "allauth.account",
    "anymail",
    # local
    "apps.common",
    "apps.accounts",
    "apps.costs",
]

# -----------------------------------------------------------------------------
# Middleware
# -----------------------------------------------------------------------------
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "allauth.account.middleware.AccountMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

# -----------------------------------------------------------------------------
# URL routing, templates, WSGI / ASGI
# -----------------------------------------------------------------------------
ROOT_URLCONF = "config.urls"

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

WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

# -----------------------------------------------------------------------------
# Database
# -----------------------------------------------------------------------------
if env("POSTGRES_HOST"):
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("POSTGRES_DB", "dumafund"),
            "USER": env("POSTGRES_USER", "dumafund"),
            "PASSWORD": env("POSTGRES_PASSWORD", "dumafund"),
            "HOST": env("POSTGRES_HOST", "localhost"),
            "PORT": env("POSTGRES_PORT", "5432"),
        }
    }
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }

if "pytest" in sys.modules:
    DATABASES["default"] = {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }

# -----------------------------------------------------------------------------
# Custom user model
# -----------------------------------------------------------------------------
AUTH_USER_MODEL = "accounts.User"

# -----------------------------------------------------------------------------
# Password validation
# -----------------------------------------------------------------------------
AUTH_PASSWORD_VALIDATORS = [
    {
        "NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.MinimumLengthValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.CommonPasswordValidator",
    },
    {
        "NAME": "django.contrib.auth.password_validation.NumericPasswordValidator",
    },
]

# -----------------------------------------------------------------------------
# Internationalization
# -----------------------------------------------------------------------------
LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

# -----------------------------------------------------------------------------
# Static & media files
# -----------------------------------------------------------------------------
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"

MEDIA_URL = "/media/"
MEDIA_ROOT = BASE_DIR / "media"

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# -----------------------------------------------------------------------------
# CORS
# -----------------------------------------------------------------------------
CORS_ALLOWED_ORIGINS = env_list(
    "CORS_ALLOWED_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173"
)
CORS_ALLOW_CREDENTIALS = True

FRONTEND_URL = env("FRONTEND_URL", "http://localhost:5173")

# -----------------------------------------------------------------------------
# OpenAI (receipt scan)
# -----------------------------------------------------------------------------
OPENAI_API_KEY = env("OPENAI_API_KEY", "")
OPENAI_RECEIPT_MODEL = env("OPENAI_RECEIPT_MODEL", "gpt-4o-mini")

# -----------------------------------------------------------------------------
# django-allauth (email verification for API registration)
# -----------------------------------------------------------------------------
SITE_ID = 1
ACCOUNT_ADAPTER = "apps.accounts.adapter.AccountAdapter"
ACCOUNT_EMAIL_VERIFICATION = "mandatory"
ACCOUNT_EMAIL_REQUIRED = True
ACCOUNT_USERNAME_REQUIRED = False
ACCOUNT_USER_MODEL_USERNAME_FIELD = None
ACCOUNT_AUTHENTICATION_METHOD = "email"
ACCOUNT_UNIQUE_EMAIL = True
ACCOUNT_EMAIL_CONFIRMATION_EXPIRE_DAYS = 3
ACCOUNT_CONFIRM_EMAIL_ON_GET = False
ACCOUNT_EMAIL_SUBJECT_PREFIX = ""
# Allow resends sooner than allauth's default 3-minute cooldown.
ACCOUNT_RATE_LIMITS = {
    "confirm_email": "1/30s/key",
}

# -----------------------------------------------------------------------------
# Email (Resend via Anymail; console fallback when no API key)
# -----------------------------------------------------------------------------
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", "DumaFund <onboarding@resend.dev>")
SERVER_EMAIL = DEFAULT_FROM_EMAIL
RESEND_API_KEY = env("RESEND_API_KEY", "")

if RESEND_API_KEY and "pytest" not in sys.modules:
    EMAIL_BACKEND = "anymail.backends.resend.EmailBackend"
    ANYMAIL = {"RESEND_API_KEY": RESEND_API_KEY}
else:
    EMAIL_BACKEND = "django.core.mail.backends.console.EmailBackend"

# -----------------------------------------------------------------------------
# OAuth (Google / GitHub)
# -----------------------------------------------------------------------------
OAUTH_REDIRECT_URI = env(
    "OAUTH_REDIRECT_URI",
    "http://localhost:8000/api/auth/oauth/{provider}/callback/",
)
GOOGLE_OAUTH_CLIENT_ID = env("GOOGLE_OAUTH_CLIENT_ID", "")
GOOGLE_OAUTH_CLIENT_SECRET = env("GOOGLE_OAUTH_CLIENT_SECRET", "")
GITHUB_OAUTH_CLIENT_ID = env("GITHUB_OAUTH_CLIENT_ID", "")
GITHUB_OAUTH_CLIENT_SECRET = env("GITHUB_OAUTH_CLIENT_SECRET", "")

# -----------------------------------------------------------------------------
# Django REST Framework
# -----------------------------------------------------------------------------
REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "apps.accounts.authentication.JWTAuthenticationWithEmailVerified",
    ),
    "DEFAULT_PERMISSION_CLASSES": (
        "rest_framework.permissions.IsAuthenticated",
        "apps.accounts.permissions.IsEmailVerified",
    ),
    "DEFAULT_PAGINATION_CLASS": "apps.common.pagination.DefaultPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_FILTER_BACKENDS": (
        "django_filters.rest_framework.DjangoFilterBackend",
        "rest_framework.filters.SearchFilter",
        "rest_framework.filters.OrderingFilter",
    ),
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "DEFAULT_THROTTLE_CLASSES": ("rest_framework.throttling.UserRateThrottle",),
    "DEFAULT_THROTTLE_RATES": {"user": "2000/hour"},
}

# -----------------------------------------------------------------------------
# JWT (SimpleJWT)
# Remember me: 90-day refresh; unchecked: 7-day refresh.
# -----------------------------------------------------------------------------
JWT_REFRESH_DAYS = int(env("JWT_REFRESH_DAYS", "90"))
JWT_REFRESH_DAYS_SHORT = int(env("JWT_REFRESH_DAYS_SHORT", "7"))
SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=int(env("JWT_ACCESS_MINUTES", "60"))),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=JWT_REFRESH_DAYS),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": False,
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

# -----------------------------------------------------------------------------
# OpenAPI (drf-spectacular)
# -----------------------------------------------------------------------------
SPECTACULAR_SETTINGS = {
    "TITLE": "DumaFund API",
    "DESCRIPTION": "DumaFund — REST API.",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
    "COMPONENT_SPLIT_REQUEST": True,
    "SCHEMA_PATH_PREFIX": "/api/",
}

# -----------------------------------------------------------------------------
# Celery
# -----------------------------------------------------------------------------
CELERY_BROKER_URL = env("CELERY_BROKER_URL")
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND")
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TASK_ALWAYS_EAGER = env_bool(
    "CELERY_TASK_ALWAYS_EAGER", not bool(env("CELERY_BROKER_URL"))
)
CELERY_TIMEZONE = TIME_ZONE
CELERY_BEAT_SCHEDULE = {}

# -----------------------------------------------------------------------------
# Cache
# -----------------------------------------------------------------------------
REDIS_URL = env("REDIS_URL")
CACHES = {
    "default": (
        {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": REDIS_URL,
        }
        if REDIS_URL
        else {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "dumafund-default",
        }
    )
}

# -----------------------------------------------------------------------------
# Object storage (local filesystem or S3-compatible)
# -----------------------------------------------------------------------------
STORAGE_BACKEND = env("STORAGE_BACKEND", "local").lower()

if STORAGE_BACKEND in {"s3", "spaces"}:
    STORAGES = {
        "default": {"BACKEND": "storages.backends.s3.S3Storage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }
    AWS_ACCESS_KEY_ID = env("AWS_ACCESS_KEY_ID")
    AWS_SECRET_ACCESS_KEY = env("AWS_SECRET_ACCESS_KEY")
    AWS_STORAGE_BUCKET_NAME = env("AWS_STORAGE_BUCKET_NAME")
    AWS_S3_REGION_NAME = env("AWS_S3_REGION_NAME", "us-east-1")
    AWS_S3_ENDPOINT_URL = env("AWS_S3_ENDPOINT_URL") or None
    AWS_S3_CUSTOM_DOMAIN = env("AWS_S3_CUSTOM_DOMAIN") or None
    AWS_DEFAULT_ACL = None
    AWS_QUERYSTRING_AUTH = True
    AWS_S3_FILE_OVERWRITE = False
else:
    STORAGES = {
        "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
        "staticfiles": {
            "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage",
        },
    }

# -----------------------------------------------------------------------------
# Logging
# -----------------------------------------------------------------------------
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "verbose": {
            "format": "{levelname} {asctime} {module} {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "verbose",
        },
    },
    "root": {
        "handlers": ["console"],
        "level": "INFO",
    },
}

# -----------------------------------------------------------------------------
# Desktop bundle — overrides applied last
# -----------------------------------------------------------------------------
DESKTOP_MODE = env_bool("APP_DESKTOP", False)

if DESKTOP_MODE:
    DEBUG = False
    ALLOWED_HOSTS = ["127.0.0.1", "localhost"]
    CSRF_TRUSTED_ORIGINS = ["http://127.0.0.1:8000", "http://localhost:8000"]

    DATA_DIR = Path(
        env(
            "APP_DATA_DIR",
            str(Path.home() / "Library/Application Support/DumaFund"),
        )
    )
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    MEDIA_ROOT = DATA_DIR / "media"
    MEDIA_ROOT.mkdir(parents=True, exist_ok=True)

    FRONTEND_DIST = Path(
        env("APP_FRONTEND_DIST", str(BASE_DIR.parent / "frontend" / "dist"))
    )

    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": env("POSTGRES_DB", "dumafund"),
            "USER": env("POSTGRES_USER", "dumafund"),
            "PASSWORD": env("POSTGRES_PASSWORD", "dumafund"),
            "HOST": env("POSTGRES_HOST", "127.0.0.1"),
            "PORT": env("POSTGRES_PORT", "5433"),
        }
    }

    CELERY_TASK_ALWAYS_EAGER = True
    CELERY_BROKER_URL = None
    CELERY_RESULT_BACKEND = None

    if "whitenoise.middleware.WhiteNoiseMiddleware" not in MIDDLEWARE:
        security_index = MIDDLEWARE.index(
            "django.middleware.security.SecurityMiddleware"
        )
        MIDDLEWARE.insert(
            security_index + 1, "whitenoise.middleware.WhiteNoiseMiddleware"
        )
