from datetime import timedelta  # noqa: E402
from pathlib import Path

import environ

from core.hosts import build_allowed_hosts

BASE_DIR = Path(__file__).resolve().parent.parent.parent
env = environ.Env()
environ.Env.read_env(BASE_DIR.parent.parent / ".env", overwrite=False)

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
# Render injects the service's public hostname; core.hosts adds it so a service
# rename cannot lock the API out of its own URL with DisallowedHost.
ALLOWED_HOSTS = build_allowed_hosts(
    env.list("DJANGO_ALLOWED_HOSTS", default=[]),
    env("RENDER_EXTERNAL_HOSTNAME", default=""),
)

INSTALLED_APPS = [
    # Django
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "django.contrib.gis",
    # Third party
    "rest_framework",
    "rest_framework_simplejwt.token_blacklist",
    "django_filters",
    "drf_spectacular",
    "corsheaders",
    "channels",
    # Shared kernel
    "core.apps.CoreConfig",
    # Domain apps
    "apps.auth",
    "apps.users",
    "apps.verification",
    "apps.vehicles",
    "apps.rides",
    "apps.bookings",
    "apps.interests",
    "apps.matching",
    "apps.ratings",
    "apps.wallet",
    "apps.payments",
    "apps.brands",
    "apps.incentives",
    "apps.moderation",
    "apps.messaging",
    "apps.notifications",
    "apps.audit",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    # Serves STATIC_ROOT in-process so the Django admin (the only verification
    # review surface) has working CSS on platforms without a web server.
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
    "core.middleware.RequestIDMiddleware",
    "apps.audit.middleware.AdminAuditMiddleware",
]

ROOT_URLCONF = "config.urls"
WSGI_APPLICATION = "config.wsgi.application"
ASGI_APPLICATION = "config.asgi.application"

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

DATABASES = {
    "default": env.db(
        "DATABASE_URL",
        default="postgis://blasa:blasa@localhost:5432/blasa",
    )
}
DATABASES["default"]["ENGINE"] = "django.contrib.gis.db.backends.postgis"

AUTH_USER_MODEL = "users.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
]

LANGUAGE_CODE = "en"
LANGUAGES = [("ar", "Arabic"), ("fr", "French"), ("en", "English")]
TIME_ZONE = "Africa/Tunis"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "DEFAULT_PERMISSION_CLASSES": [
        "rest_framework.permissions.IsAuthenticated",
    ],
    "DEFAULT_PAGINATION_CLASS": "core.pagination.StandardPagination",
    "PAGE_SIZE": 20,
    "DEFAULT_FILTER_BACKENDS": ["django_filters.rest_framework.DjangoFilterBackend"],
    "DEFAULT_SCHEMA_CLASS": "config.schema.DomainTagAutoSchema",
    "EXCEPTION_HANDLER": "core.exceptions.custom_exception_handler",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {"anon": "30/min", "user": "300/min", "otp": "10/min", "login": "10/min"},
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env.int("JWT_ACCESS_MINUTES", default=15)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.int("JWT_REFRESH_DAYS", default=30)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Blasa API",
    "VERSION": "1.0.0",
    "DESCRIPTION": (
        "Ride-sharing and carpooling platform for Tunisia (Tunis, Sousse). "
        "Phone-number OTP auth, verified drivers, rides and bookings, "
        "a double-entry wallet ledger and gateway payments. Amounts are TND in millimes (3 decimals)."
    ),
    "SERVE_INCLUDE_SCHEMA": False,
    "SERVE_PERMISSIONS": ["rest_framework.permissions.AllowAny"],
    "SERVERS": [{"url": "http://localhost:8000", "description": "Local development"}],
    "TAGS": [
        {"name": "auth", "description": "OTP login, token refresh, logout"},
        {"name": "users", "description": "Profile and driver upgrade"},
        {"name": "verification", "description": "CIN and Carte Grise documents"},
        {"name": "vehicles", "description": "Driver vehicles"},
        {"name": "rides", "description": "Published trips and nearby search"},
        {"name": "bookings", "description": "Booking lifecycle and transitions"},
        {"name": "interests", "description": "Passenger standing requests for future routes"},
        {"name": "matching", "description": "Geo-scored ride/interest matches"},
        {"name": "wallet", "description": "Balance and ledger history"},
        {"name": "payments", "description": "Topups and gateway webhooks"},
    ],
    # Stable component names for enums whose field name ("status", "kind") is reused
    # with different choice sets; otherwise the generator invents names like
    # "Status408Enum" that churn on every unrelated change.
    "ENUM_NAME_OVERRIDES": {
        "UserRoleEnum": "apps.users.models.USER_ROLE_CHOICES",
        "RideStatusEnum": "apps.rides.models.RIDE_STATUS_CHOICES",
        "IncentiveAwardKindEnum": "apps.incentives.models.INCENTIVE_AWARD_KIND_CHOICES",
    },
}

# Flouci payment gateway
FLUOCI_BASE_URL = env("FLUOCI_BASE_URL", default="https://pay.flouci.com")
FLUOCI_APP_KEY = env("FLUOCI_APP_KEY", default="")
FLUOCI_PRIVATE_KEY = env("FLUOCI_PRIVATE_KEY", default="")
FLUOCI_PUBLIC_KEY = env("FLUOCI_PUBLIC_KEY", default="")
FLUOCI_WEBHOOK_SECRET = env("FLUOCI_WEBHOOK_SECRET", default="dev-flouci-webhook-secret")

FCM_CREDENTIALS_PATH = env("FCM_CREDENTIALS_PATH", default=None)
EXPO_PUSH_ENABLED = env.bool("EXPO_PUSH_ENABLED", default=False)
NOTIFICATION_EMAIL_ENABLED = env.bool("NOTIFICATION_EMAIL_ENABLED", default=False)

CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])

REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")
# Upstash (and any managed Redis) hands out a TLS URL. channels_redis needs an
# explicit host dict to accept the certificate chain there.
REDIS_TLS = REDIS_URL.startswith("rediss://")

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [{"address": REDIS_URL, "ssl_cert_reqs": None}] if REDIS_TLS else [REDIS_URL],
        },
    }
}

CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
# Platforms without a free background worker (Render) run tasks inline instead.
CELERY_TASK_ALWAYS_EAGER = env.bool("CELERY_TASK_ALWAYS_EAGER", default=False)
# Upstash drops idle connections; retry instead of crashing the worker on boot.
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_BROKER_TRANSPORT_OPTIONS = {"socket_timeout": env.int("CELERY_SOCKET_TIMEOUT", default=10)}
CELERY_BEAT_SCHEDULE = {
    "ride-reminders": {"task": "apps.notifications.tasks.schedule_ride_reminders", "schedule": 60.0},
    "notification-retries": {"task": "apps.notifications.tasks.retry_deliveries", "schedule": 60.0},
}

# Email transport is opt-in. Auth tasks never print OTPs through console/file backends.
EMAIL_BACKEND = env("EMAIL_BACKEND", default="django.core.mail.backends.smtp.EmailBackend")
if EMAIL_BACKEND == "django.core.mail.backends.console.EmailBackend" and not DEBUG:
    EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_HOST = env("EMAIL_HOST", default="")
EMAIL_PORT = env.int("EMAIL_PORT", default=587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", default="")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", default="")
EMAIL_USE_SSL = env.bool("EMAIL_USE_SSL", default=False)
EMAIL_USE_TLS = env.bool("EMAIL_USE_TLS", default=not EMAIL_USE_SSL)
EMAIL_TIMEOUT = env.int("EMAIL_TIMEOUT", default=10)
DEFAULT_FROM_EMAIL = env("DEFAULT_FROM_EMAIL", default="")

# --- Brevo (free transactional email + SMS over HTTPS) ---------------------
# Render's free tier blocks outbound SMTP, so a configured API key switches the
# email backend and the SMS adapter to Brevo's HTTP API automatically.
BREVO_API_KEY = env("BREVO_API_KEY", default="")
BREVO_SMS_SENDER = env("BREVO_SMS_SENDER", default="Blasa")
SMS_PROVIDER = env("SMS_PROVIDER", default="brevo" if BREVO_API_KEY else "console")
SMS_TIMEOUT = env.int("SMS_TIMEOUT", default=10)
if BREVO_API_KEY and env.bool("BREVO_EMAIL_ENABLED", default=True):
    EMAIL_BACKEND = "core.delivery.brevo.BrevoEmailBackend"

# Private S3 bucket for identity and vehicle documents
S3_BUCKET_PRIVATE = env("S3_BUCKET_PRIVATE", default="blasa-private-docs")
S3_ENDPOINT_URL = env("S3_ENDPOINT_URL", default=None)
# Required for S3-compatible providers: Cloudflare R2 and Backblaze B2 both use
# "auto", MinIO accepts any non-empty value. boto3 cannot sign without a region.
S3_REGION = env("S3_REGION", default=None)

# Optional default file storage. Verification documents already use presigned
# boto3 URLs against S3_BUCKET_PRIVATE; this only affects Django FileFields
# (none today) and stays off unless explicitly enabled.
USE_S3_STORAGE = env.bool("USE_S3_STORAGE", default=False)
if USE_S3_STORAGE:
    STORAGES = {
        "default": {
            "BACKEND": "storages.backends.s3.S3Storage",
            "OPTIONS": {
                "bucket_name": S3_BUCKET_PRIVATE,
                "endpoint_url": S3_ENDPOINT_URL,
                "region_name": S3_REGION,
                "default_acl": None,
                "querystring_auth": True,
                "file_overwrite": False,
            },
        },
        "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
    }

SECURE_CONTENT_TYPE_NOSNIFF = True
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG

# --- Error monitoring (Sentry, free tier) ---------------------------------
SENTRY_DSN = env("SENTRY_DSN", default="")
SENTRY_ENVIRONMENT = env("SENTRY_ENVIRONMENT", default="development" if DEBUG else "production")
SENTRY_RELEASE = env("SENTRY_RELEASE", default="")
SENTRY_TRACES_RATE = env.float("SENTRY_TRACES_RATE", default=0.05)

# Identity documents (CIN, Carte Grise) upload gate. Kept OFF in production
# until the law 2004-63 checklist in docs/legal is signed off by counsel.
IDENTITY_DOCUMENT_UPLOADS_ENABLED = env.bool("IDENTITY_DOCUMENT_UPLOADS_ENABLED", default=DEBUG)

# Structured logging: every record carries the request id from RequestIDMiddleware.
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "filters": {
        "request_id": {"()": "core.log_filters.RequestIDFilter"},
        "redact_tokens": {"()": "core.log_filters.RedactTokenFilter"},
    },
    "formatters": {
        "structured": {
            "format": "{levelname} {asctime} {name} [{request_id}] {message}",
            "style": "{",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "filters": ["request_id", "redact_tokens"],
            "formatter": "structured",
        },
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    "loggers": {
        "django.request": {"level": "WARNING"},
        "blasa": {"level": "INFO"},
    },
}
