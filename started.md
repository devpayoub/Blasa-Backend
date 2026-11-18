# Blasa Backend Starter (Django)

Starter for the Blasa ride-sharing platform. It uses Django 5, Django REST Framework, GeoDjango (PostGIS), Django Channels, and Celery. Each domain is a Django app, and business logic lives in services.

---

## 1. Repository setup

### `apps/api/requirements/base.txt`

```text
Django>=5.0,<5.3
djangorestframework>=3.15
djangorestframework-simplejwt>=5.3
drf-spectacular>=0.27
django-filter>=24.2
psycopg[binary]>=3.1
channels>=4.1
daphne>=4.1
channels-redis>=4.2
redis>=5.0
celery>=5.4
django-environ>=0.11
django-cors-headers>=4.4
django-storages[s3]>=1.14
boto3>=1.34
firebase-admin>=6.5
gunicorn>=22.0
```

### `apps/api/requirements/dev.txt`

```text
-r base.txt
pytest>=8.0
pytest-django>=4.8
factory-boy>=3.3
ruff>=0.5
```

### `infra/docker/docker-compose.yml`

```yaml
services:
  db:
    image: postgis/postgis:16-3.4
    environment:
      POSTGRES_DB: blasa
      POSTGRES_USER: blasa
      POSTGRES_PASSWORD: blasa
    ports:
      - "5432:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data

  redis:
    image: redis:7-alpine
    ports:
      - "6379:6379"

  api:
    build:
      context: ../../apps/api
      dockerfile: ../../infra/docker/api.Dockerfile
    env_file: ../../.env
    command: daphne -b 0.0.0.0 -p 8000 config.asgi:application
    depends_on: [db, redis]
    ports:
      - "8000:8000"
    volumes:
      - ../../apps/api:/app

  worker:
    build:
      context: ../../apps/api
      dockerfile: ../../infra/docker/api.Dockerfile
    env_file: ../../.env
    command: celery -A config worker -l info
    depends_on: [db, redis]
    volumes:
      - ../../apps/api:/app

volumes:
  pgdata:
```

### `infra/docker/api.Dockerfile`

```dockerfile
FROM python:3.12-slim

RUN apt-get update && apt-get install -y --no-install-recommends \
    gdal-bin libgdal-dev binutils libproj-dev \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements/ requirements/
RUN pip install --no-cache-dir -r requirements/dev.txt

COPY . .
EXPOSE 8000
```

### `.env.example`

```bash
DJANGO_SECRET_KEY=change-me
DJANGO_DEBUG=1
DJANGO_ALLOWED_HOSTS=localhost,127.0.0.1
DATABASE_URL=postgis://blasa:blasa@db:5432/blasa
REDIS_URL=redis://redis:6379/0
CORS_ALLOWED_ORIGINS=http://localhost:5173
JWT_ACCESS_MINUTES=15
JWT_REFRESH_DAYS=30
S3_BUCKET_PRIVATE=blasa-private-docs
S3_ENDPOINT_URL=
FCM_CREDENTIALS_PATH=
SMS_PROVIDER=twilio
```

### `Makefile`

```makefile
up:
	docker compose -f infra/docker/docker-compose.yml up -d --build

down:
	docker compose -f infra/docker/docker-compose.yml down

migrate:
	docker compose -f infra/docker/docker-compose.yml exec api python manage.py migrate

test:
	docker compose -f infra/docker/docker-compose.yml exec api pytest

lint:
	docker compose -f infra/docker/docker-compose.yml exec api ruff check .
```

---

## 2. Project configuration

### `apps/api/manage.py`

```python
#!/usr/bin/env python
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()
```

### `apps/api/config/settings/base.py`

```python
from pathlib import Path

import environ

BASE_DIR = Path(__file__).resolve().parent.parent.parent
env = environ.Env()
environ.Env.read_env(BASE_DIR.parent.parent / ".env", overwrite=False)

SECRET_KEY = env("DJANGO_SECRET_KEY")
DEBUG = env.bool("DJANGO_DEBUG", default=False)
ALLOWED_HOSTS = env.list("DJANGO_ALLOWED_HOSTS", default=[])

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
    "django_filters",
    "drf_spectacular",
    "corsheaders",
    "channels",
    # Shared kernel
    "core",
    # Domain apps
    "apps.users",
    "apps.verification",
    "apps.vehicles",
    "apps.rides",
    "apps.bookings",
    "apps.matching",
    "apps.wallet",
    "apps.payments",
    "apps.brands",
    "apps.messaging",
    "apps.notifications",
    "apps.audit",
]

MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
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
    "DEFAULT_SCHEMA_CLASS": "drf_spectacular.openapi.AutoSchema",
    "EXCEPTION_HANDLER": "core.exceptions.custom_exception_handler",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {"anon": "30/min", "user": "300/min"},
}

from datetime import timedelta  # noqa: E402

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=env.int("JWT_ACCESS_MINUTES", default=15)),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=env.int("JWT_REFRESH_DAYS", default=30)),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
}

SPECTACULAR_SETTINGS = {
    "TITLE": "Blasa API",
    "VERSION": "1.0.0",
    "SERVE_INCLUDE_SCHEMA": False,
}

CORS_ALLOWED_ORIGINS = env.list("CORS_ALLOWED_ORIGINS", default=[])

REDIS_URL = env("REDIS_URL", default="redis://localhost:6379/0")

CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {"hosts": [REDIS_URL]},
    }
}

CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
CELERY_TASK_ALWAYS_EAGER = False

# Private S3 bucket for identity and vehicle documents
S3_BUCKET_PRIVATE = env("S3_BUCKET_PRIVATE", default="blasa-private-docs")
S3_ENDPOINT_URL = env("S3_ENDPOINT_URL", default=None)
SECURE_CONTENT_TYPE_NOSNIFF = True
SESSION_COOKIE_SECURE = not DEBUG
CSRF_COOKIE_SECURE = not DEBUG
```

### `apps/api/config/settings/dev.py`

```python
from .base import *  # noqa: F401,F403

DEBUG = True
```

### `apps/api/config/settings/prod.py`

```python
from .base import *  # noqa: F401,F403

DEBUG = False
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
```

### `apps/api/config/settings/test.py`

```python
from .base import *  # noqa: F401,F403

DEBUG = False
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
CELERY_TASK_ALWAYS_EAGER = True
```

### `apps/api/config/urls.py`

```python
from django.conf import settings
from django.contrib import admin
from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

urlpatterns = [
    path("admin/", admin.site.urls),
    path("api/v1/", include("api.v1.urls")),
    path("api/schema/", SpectacularAPIView.as_view(), name="schema"),
    path("api/docs/", SpectacularSwaggerView.as_view(url_name="schema"), name="docs"),
]

if settings.DEBUG:
    from django.conf.urls.static import static
    urlpatterns += static(settings.STATIC_URL, document_root=settings.STATIC_ROOT)
```

### `apps/api/config/asgi.py`

```python
import os

from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from django.core.asgi import get_asgi_application

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")
django_asgi_app = get_asgi_application()

from apps.messaging.routing import websocket_urlpatterns  # noqa: E402

application = ProtocolTypeRouter(
    {
        "http": django_asgi_app,
        "websocket": AuthMiddlewareStack(URLRouter(websocket_urlpatterns)),
    }
)
```

### `apps/api/config/celery.py`

```python
import os

from celery import Celery

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "config.settings.dev")

app = Celery("blasa")
app.config_from_object("django.conf:settings", namespace="CELERY")
app.autodiscover_tasks()
```

---

## 3. Shared kernel (`core`)

### `apps/api/core/models.py`

```python
import uuid

from django.db import models


class UUIDModel(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    class Meta:
        abstract = True


class TimeStampedModel(UUIDModel):
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True
```

### `apps/api/core/pagination.py`

```python
from rest_framework.pagination import PageNumberPagination


class StandardPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100
```

### `apps/api/core/exceptions.py`

```python
from rest_framework.views import exception_handler


def custom_exception_handler(exc, context):
    response = exception_handler(exc, context)
    if response is not None:
        response.data = {
            "error": {
                "status": response.status_code,
                "detail": response.data,
            }
        }
    return response
```

### `apps/api/core/middleware.py`

```python
import uuid


class RequestIDMiddleware:
    header = "HTTP_X_REQUEST_ID"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = request.META.get(self.header) or str(uuid.uuid4())
        request.request_id = request_id
        response = self.get_response(request)
        response["X-Request-ID"] = request_id
        return response
```

### `apps/api/core/events.py`

```python
from collections import defaultdict
from typing import Callable

_handlers: dict[str, list[Callable]] = defaultdict(list)


def subscribe(event_name: str, handler: Callable) -> None:
    _handlers[event_name].append(handler)


def publish(event_name: str, **payload) -> None:
    for handler in _handlers[event_name]:
        handler(**payload)
```

---

## 4. Users (`apps/users`)

### `apps/api/apps/users/models.py`

```python
from django.contrib.auth.models import AbstractUser, BaseUserManager
from django.db import models

from core.models import TimeStampedModel


class UserManager(BaseUserManager):
    use_in_migrations = True

    def create_user(self, phone_number, password=None, **extra):
        if not phone_number:
            raise ValueError("Phone number is required")
        user = self.model(phone_number=phone_number, **extra)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, phone_number, password=None, **extra):
        extra.setdefault("is_staff", True)
        extra.setdefault("is_superuser", True)
        return self.create_user(phone_number, password, **extra)


class User(AbstractUser, TimeStampedModel):
    """Phone-number-based account. Tunisian mobile numbers are the primary login."""

    username = None
    phone_number = models.CharField(max_length=20, unique=True)
    email = models.EmailField(blank=True)

    class Role(models.TextChoices):
        PASSENGER = "PASSENGER", "Passenger"
        DRIVER = "DRIVER", "Driver"
        BRAND = "BRAND", "Brand"

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.PASSENGER)
    preferred_language = models.CharField(
        max_length=2, choices=[("ar", "Arabic"), ("fr", "French"), ("en", "English")], default="fr"
    )
    is_verified = models.BooleanField(default=False)

    USERNAME_FIELD = "phone_number"
    REQUIRED_FIELDS = []

    objects = UserManager()

    def __str__(self):
        return self.phone_number


class Profile(TimeStampedModel):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="profile")
    full_name = models.CharField(max_length=150)
    avatar_key = models.CharField(max_length=255, blank=True)
    bio = models.TextField(blank=True, max_length=500)
    average_rating = models.DecimalField(max_digits=3, decimal_places=2, default=0)
```

### `apps/api/apps/users/serializers.py`

```python
from rest_framework import serializers

from .models import Profile, User


class ProfileSerializer(serializers.ModelSerializer):
    class Meta:
        model = Profile
        fields = ["full_name", "avatar_key", "bio", "average_rating"]
        read_only_fields = ["average_rating"]


class UserSerializer(serializers.ModelSerializer):
    profile = ProfileSerializer(read_only=True)

    class Meta:
        model = User
        fields = ["id", "phone_number", "role", "preferred_language", "is_verified", "profile"]
        read_only_fields = ["id", "phone_number", "role", "is_verified"]
```

### `apps/api/apps/users/views.py`

```python
from rest_framework import generics

from .serializers import UserSerializer


class MeView(generics.RetrieveUpdateAPIView):
    serializer_class = UserSerializer

    def get_object(self):
        return self.request.user
```

### `apps/api/apps/users/urls.py`

```python
from django.urls import path

from .views import MeView

urlpatterns = [
    path("me/", MeView.as_view(), name="me"),
]
```

### `apps/api/apps/users/admin.py`

```python
from django.contrib import admin

from .models import Profile, User


class ProfileInline(admin.StackedInline):
    model = Profile
    can_delete = False


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ["phone_number", "role", "is_verified", "is_active", "date_joined"]
    list_filter = ["role", "is_verified", "is_active"]
    search_fields = ["phone_number", "email", "profile__full_name"]
    inlines = [ProfileInline]
```

---

## 5. Verification (`apps/verification`)

Documents are sensitive. Files go to a private bucket, and the database stores only the object key and a review status. Access happens through signed URLs.

### `apps/api/apps/verification/models.py`

```python
from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


class VerificationDocument(TimeStampedModel):
    class DocType(models.TextChoices):
        CIN = "CIN", "National ID (CIN)"
        CARTE_GRISE = "CARTE_GRISE", "Vehicle registration (Carte Grise)"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending review"
        APPROVED = "APPROVED", "Approved"
        REJECTED = "REJECTED", "Rejected"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="documents")
    doc_type = models.CharField(max_length=20, choices=DocType.choices)
    storage_key = models.CharField(max_length=512)  # Object key only, never a public URL
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    rejection_reason = models.TextField(blank=True)

    class Meta:
        indexes = [models.Index(fields=["user", "doc_type"])]
```

### `apps/api/apps/verification/services.py`

```python
from django.db import transaction
from django.utils import timezone

from core.events import publish

from .models import VerificationDocument


@transaction.atomic
def submit_document(*, user, doc_type, storage_key):
    doc = VerificationDocument.objects.create(user=user, doc_type=doc_type, storage_key=storage_key)
    publish("verification.submitted", document_id=str(doc.id))
    return doc


@transaction.atomic
def review_document(*, document, reviewer, approve: bool, reason: str = ""):
    if document.status != VerificationDocument.Status.PENDING:
        raise ValueError("Document has already been reviewed")

    document.status = (
        VerificationDocument.Status.APPROVED if approve else VerificationDocument.Status.REJECTED
    )
    document.reviewed_by = reviewer
    document.reviewed_at = timezone.now()
    document.rejection_reason = "" if approve else reason
    document.save(update_fields=["status", "reviewed_by", "reviewed_at", "rejection_reason", "updated_at"])
    return document
```

### `apps/api/apps/verification/storage.py`

```python
import boto3
from django.conf import settings


def signed_download_url(storage_key: str, expires_in: int = 300) -> str:
    """Return a short-lived URL for a private document. Never expose raw keys."""
    client = boto3.client("s3", endpoint_url=settings.S3_ENDPOINT_URL)
    return client.generate_presigned_url(
        "get_object",
        Params={"Bucket": settings.S3_BUCKET_PRIVATE, "Key": storage_key},
        ExpiresIn=expires_in,
    )
```

### `apps/api/apps/verification/admin.py`

```python
from django.contrib import admin
from django.utils import timezone

from .models import VerificationDocument
from .services import review_document


@admin.register(VerificationDocument)
class VerificationDocumentAdmin(admin.ModelAdmin):
    list_display = ["user", "doc_type", "status", "reviewed_by", "created_at"]
    list_filter = ["doc_type", "status"]
    search_fields = ["user__phone_number"]
    readonly_fields = ["storage_key", "reviewed_by", "reviewed_at", "created_at"]
    actions = ["approve_selected", "reject_selected"]

    @admin.action(description="Approve selected documents")
    def approve_selected(self, request, queryset):
        for doc in queryset.filter(status=VerificationDocument.Status.PENDING):
            review_document(document=doc, reviewer=request.user, approve=True)

    @admin.action(description="Reject selected documents")
    def reject_selected(self, request, queryset):
        for doc in queryset.filter(status=VerificationDocument.Status.PENDING):
            review_document(document=doc, reviewer=request.user, approve=False, reason="Rejected by admin")
```

---

## 6. Wallet ledger (`apps/wallet`)

The wallet is a double-entry ledger. Entries are append-only, every transaction must balance to zero, and balances are derived from entries.

### `apps/api/apps/wallet/models.py`

```python
from decimal import Decimal

from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


class Account(TimeStampedModel):
    """A ledger account: user wallets plus system accounts (revenue, clearing, sponsor pool)."""

    class Kind(models.TextChoices):
        USER = "USER", "User wallet"
        SYSTEM = "SYSTEM", "System account"

    kind = models.CharField(max_length=10, choices=Kind.choices)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="ledger_accounts"
    )
    code = models.CharField(max_length=50, unique=True)  # e.g. "user:<uuid>", "sys:platform_revenue"
    currency = models.CharField(max_length=3, default="TND")
    allow_negative = models.BooleanField(default=False)

    def __str__(self):
        return self.code


class Transaction(TimeStampedModel):
    """Groups ledger entries that must balance together."""

    kind = models.CharField(max_length=40)  # e.g. RIDE_PAYMENT, TOPUP, REFUND, SPONSOR_SUBSIDY
    reference = models.CharField(max_length=120, unique=True)  # Idempotency key
    description = models.CharField(max_length=255, blank=True)


class LedgerEntry(TimeStampedModel):
    transaction = models.ForeignKey(Transaction, on_delete=models.PROTECT, related_name="entries")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="entries")
    amount = models.DecimalField(max_digits=14, decimal_places=3)  # Positive = credit, negative = debit (millimes)

    class Meta:
        indexes = [models.Index(fields=["account", "created_at"])]
```

### `apps/api/apps/wallet/constraints.py`

```python
from django.db import models


class ImmutableQuerySet(models.QuerySet):
    def update(self, **kwargs):
        raise PermissionError("Ledger entries are immutable")

    def delete(self):
        raise PermissionError("Ledger entries are immutable")
```

### `apps/api/apps/wallet/services.py`

```python
from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Sum

from .models import Account, LedgerEntry, Transaction


class InsufficientFunds(Exception):
    pass


def balance_of(account: Account) -> Decimal:
    total = account.entries.aggregate(total=Sum("amount"))["total"]
    return total or Decimal("0")


@transaction.atomic
def post_transaction(*, kind: str, reference: str, legs: list[tuple[Account, Decimal]], description: str = ""):
    """
    Post a balanced set of ledger legs.
    legs: list of (account, amount). Amounts must sum to zero.
    Idempotent: posting the same reference twice returns the existing transaction.
    """
    if sum(amount for _, amount in legs) != 0:
        raise ValueError("Ledger transaction does not balance")

    existing = Transaction.objects.filter(reference=reference).first()
    if existing:
        return existing

    # Lock accounts in a stable order to prevent deadlocks
    accounts = sorted({acc.pk: acc for acc, _ in legs}.values(), key=lambda a: a.pk)
    locked = {a.pk: a for a in Account.objects.select_for_update().filter(pk__in=[a.pk for a in accounts])}

    for account, amount in legs:
        acc = locked[account.pk]
        if amount < 0 and not acc.allow_negative and balance_of(acc) + amount < 0:
            raise InsufficientFunds(f"Account {acc.code} has insufficient funds")

    try:
        txn = Transaction.objects.create(kind=kind, reference=reference, description=description)
    except IntegrityError:
        return Transaction.objects.get(reference=reference)

    LedgerEntry.objects.bulk_create(
        [LedgerEntry(transaction=txn, account=locked[acc.pk], amount=amount) for acc, amount in legs]
    )
    return txn


def transfer(*, source: Account, destination: Account, amount: Decimal, kind: str, reference: str, description=""):
    if amount <= 0:
        raise ValueError("Transfer amount must be positive")
    return post_transaction(
        kind=kind,
        reference=reference,
        legs=[(source, -amount), (destination, amount)],
        description=description,
    )
```

### `apps/api/apps/wallet/admin.py`

```python
from django.contrib import admin

from .models import Account, LedgerEntry, Transaction


@admin.register(Account)
class AccountAdmin(admin.ModelAdmin):
    list_display = ["code", "kind", "currency", "allow_negative"]
    search_fields = ["code"]


class LedgerEntryInline(admin.TabularInline):
    model = LedgerEntry
    extra = 0
    readonly_fields = ["account", "amount"]
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ["reference", "kind", "created_at"]
    search_fields = ["reference"]
    inlines = [LedgerEntryInline]

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
```

---

## 7. Bookings (`apps/bookings`)

The booking state machine is explicit. Only the transitions defined in `TRANSITIONS` are allowed, and every transition writes a `BookingEvent`.

### `apps/api/apps/bookings/state_machine.py`

```python
class BookingStatus:
    REQUESTED = "REQUESTED"
    ACCEPTED = "ACCEPTED"
    CONFIRMED = "CONFIRMED"
    COMPLETED = "COMPLETED"
    REJECTED = "REJECTED"
    CANCELLED_BY_PASSENGER = "CANCELLED_BY_PASSENGER"
    CANCELLED_BY_DRIVER = "CANCELLED_BY_DRIVER"
    NO_SHOW = "NO_SHOW"

    CHOICES = [
        (REQUESTED, "Requested"),
        (ACCEPTED, "Accepted"),
        (CONFIRMED, "Confirmed"),
        (COMPLETED, "Completed"),
        (REJECTED, "Rejected"),
        (CANCELLED_BY_PASSENGER, "Cancelled by passenger"),
        (CANCELLED_BY_DRIVER, "Cancelled by driver"),
        (NO_SHOW, "No show"),
    ]


TERMINAL = {
    BookingStatus.COMPLETED,
    BookingStatus.REJECTED,
    BookingStatus.CANCELLED_BY_PASSENGER,
    BookingStatus.CANCELLED_BY_DRIVER,
    BookingStatus.NO_SHOW,
}

S = BookingStatus
TRANSITIONS = {
    (S.REQUESTED, "accept"): S.ACCEPTED,
    (S.REQUESTED, "reject"): S.REJECTED,
    (S.REQUESTED, "cancel_passenger"): S.CANCELLED_BY_PASSENGER,
    (S.ACCEPTED, "confirm"): S.CONFIRMED,
    (S.ACCEPTED, "cancel_passenger"): S.CANCELLED_BY_PASSENGER,
    (S.ACCEPTED, "cancel_driver"): S.CANCELLED_BY_DRIVER,
    (S.CONFIRMED, "complete"): S.COMPLETED,
    (S.CONFIRMED, "no_show"): S.NO_SHOW,
    (S.CONFIRMED, "cancel_passenger"): S.CANCELLED_BY_PASSENGER,
    (S.CONFIRMED, "cancel_driver"): S.CANCELLED_BY_DRIVER,
}


class InvalidTransition(Exception):
    pass


def next_status(current: str, action: str) -> str:
    if current in TERMINAL:
        raise InvalidTransition(f"Booking is in terminal state {current}")
    try:
        return TRANSITIONS[(current, action)]
    except KeyError:
        raise InvalidTransition(f"Action '{action}' not allowed from {current}")
```

### `apps/api/apps/bookings/models.py`

```python
from django.conf import settings
from django.db import models

from core.models import TimeStampedModel

from .state_machine import BookingStatus


class Booking(TimeStampedModel):
    ride = models.ForeignKey("rides.Ride", on_delete=models.PROTECT, related_name="bookings")
    passenger = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="bookings")
    seats = models.PositiveSmallIntegerField(default=1)
    pickup_point = models.PointField(geography=True, srid=4326)
    dropoff_point = models.PointField(geography=True, srid=4326)
    status = models.CharField(max_length=30, choices=BookingStatus.CHOICES, default=BookingStatus.REQUESTED, db_index=True)
    fare_amount = models.DecimalField(max_digits=10, decimal_places=3)

    class Meta:
        constraints = [
            models.CheckConstraint(check=models.Q(seats__gte=1), name="booking_seats_positive"),
        ]


class BookingEvent(TimeStampedModel):
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="events")
    from_status = models.CharField(max_length=30)
    to_status = models.CharField(max_length=30)
    action = models.CharField(max_length=30)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
```

Note: `models.PointField` needs `from django.contrib.gis.db import models`. Replace the import in this file to use GeoDjango fields.

### `apps/api/apps/bookings/services.py`

```python
from django.db import transaction

from core.events import publish

from .models import Booking, BookingEvent
from .state_machine import next_status


@transaction.atomic
def apply_action(*, booking: Booking, action: str, actor) -> Booking:
    # Lock the row so two actors cannot transition the same booking at once
    booking = Booking.objects.select_for_update().get(pk=booking.pk)
    previous = booking.status
    new_status = next_status(previous, action)

    booking.status = new_status
    booking.save(update_fields=["status", "updated_at"])
    BookingEvent.objects.create(
        booking=booking, from_status=previous, to_status=new_status, action=action, actor=actor
    )
    publish("booking.status_changed", booking_id=str(booking.id), status=new_status)
    return booking
```

### `apps/api/apps/bookings/views.py`

```python
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from . import services
from .models import Booking
from .state_machine import InvalidTransition
from .serializers import BookingSerializer


class BookingViewSet(mixins.CreateModelMixin, mixins.RetrieveModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = BookingSerializer

    def get_queryset(self):
        user = self.request.user
        return Booking.objects.filter(passenger=user) | Booking.objects.filter(ride__driver=user)

    def perform_create(self, serializer):
        serializer.save(passenger=self.request.user)

    @action(detail=True, methods=["post"], url_path=r"transitions/(?P<name>[a-z_]+)")
    def transition(self, request, pk=None, name=None):
        booking = self.get_object()
        try:
            booking = services.apply_action(booking=booking, action=name, actor=request.user)
        except InvalidTransition as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_409_CONFLICT)
        return Response(BookingSerializer(booking).data)
```

### `apps/api/apps/bookings/serializers.py`

```python
from rest_framework import serializers

from .models import Booking


class BookingSerializer(serializers.ModelSerializer):
    class Meta:
        model = Booking
        fields = ["id", "ride", "passenger", "seats", "pickup_point", "dropoff_point", "status", "fare_amount"]
        read_only_fields = ["id", "passenger", "status", "fare_amount"]
```

---

## 8. Messaging (`apps/messaging`)

### `apps/api/apps/messaging/routing.py`

```python
from django.urls import path

from .consumers import ChatConsumer

websocket_urlpatterns = [
    path("ws/chat/<uuid:conversation_id>/", ChatConsumer.as_asgi()),
]
```

### `apps/api/apps/messaging/consumers.py`

```python
from channels.generic.websocket import AsyncJsonWebsocketConsumer


class ChatConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        if not user or not user.is_authenticated:
            await self.close(code=4401)
            return
        self.group_name = f"chat_{self.scope['url_route']['kwargs']['conversation_id']}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive_json(self, content, **kwargs):
        await self.channel_layer.group_send(
            self.group_name,
            {"type": "chat.message", "body": content.get("body", ""), "sender": str(self.scope["user"].id)},
        )

    async def chat_message(self, event):
        await self.send_json({"body": event["body"], "sender": event["sender"]})
```

Note: production code should validate membership in the conversation during `connect` and persist messages before broadcasting.

---

## 9. Audit (`apps/audit`)

### `apps/api/apps/audit/models.py`

```python
from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


class AuditLog(TimeStampedModel):
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
    method = models.CharField(max_length=10)
    path = models.CharField(max_length=500)
    status_code = models.PositiveSmallIntegerField()
    request_id = models.CharField(max_length=64, blank=True)
    ip_hash = models.CharField(max_length=64, blank=True)  # Hashed, not raw IP

    def save(self, *args, **kwargs):
        if self.pk and AuditLog.objects.filter(pk=self.pk).exists():
            raise PermissionError("Audit logs are append-only")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Audit logs cannot be deleted")
```

### `apps/api/apps/audit/middleware.py`

```python
import hashlib

from .models import AuditLog


class AdminAuditMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if request.path.startswith("/admin/") and request.user.is_authenticated and request.user.is_staff:
            ip = request.META.get("REMOTE_ADDR", "")
            AuditLog.objects.create(
                actor=request.user,
                method=request.method,
                path=request.path[:500],
                status_code=response.status_code,
                request_id=getattr(request, "request_id", ""),
                ip_hash=hashlib.sha256(ip.encode()).hexdigest(),
            )
        return response
```

---

## 10. API routing (`apps/api/api/v1`)

### `apps/api/api/__init__.py`

```python
```

### `apps/api/api/v1/__init__.py`

```python
```

### `apps/api/api/v1/urls.py`

```python
from django.urls import include, path

urlpatterns = [
    path("users/", include("apps.users.urls")),
    path("bookings/", include("apps.bookings.urls")),
]
```

### `apps/api/apps/bookings/urls.py`

```python
from rest_framework.routers import DefaultRouter

from .views import BookingViewSet

router = DefaultRouter()
router.register("", BookingViewSet, basename="booking")

urlpatterns = router.urls
```

---

## 11. Tests (`apps/api/tests`)

### `apps/api/tests/test_booking_state_machine.py`

```python
import pytest

from apps.bookings.state_machine import BookingStatus, InvalidTransition, next_status


def test_happy_path_transitions():
    status = BookingStatus.REQUESTED
    status = next_status(status, "accept")
    status = next_status(status, "confirm")
    status = next_status(status, "complete")
    assert status == BookingStatus.COMPLETED


def test_terminal_state_rejects_actions():
    with pytest.raises(InvalidTransition):
        next_status(BookingStatus.COMPLETED, "cancel_passenger")


def test_invalid_action_from_requested():
    with pytest.raises(InvalidTransition):
        next_status(BookingStatus.REQUESTED, "complete")
```

### `apps/api/tests/test_wallet_ledger.py`

```python
from decimal import Decimal

import pytest

from apps.wallet.models import Account
from apps.wallet.services import InsufficientFunds, post_transaction, transfer


@pytest.mark.django_db
def test_unbalanced_transaction_is_rejected():
    a = Account.objects.create(kind="SYSTEM", code="sys:a")
    with pytest.raises(ValueError):
        post_transaction(kind="TEST", reference="t1", legs=[(a, Decimal("10"))])


@pytest.mark.django_db
def test_transfer_respects_balance():
    funder = Account.objects.create(kind="SYSTEM", code="sys:funder", allow_negative=True)
    user = Account.objects.create(kind="USER", code="user:1")
    sink = Account.objects.create(kind="SYSTEM", code="sys:sink", allow_negative=True)

    transfer(source=funder, destination=user, amount=Decimal("20"), kind="TOPUP", reference="topup-1")
    transfer(source=user, destination=sink, amount=Decimal("5"), kind="RIDE_PAYMENT", reference="ride-1")

    with pytest.raises(InsufficientFunds):
        transfer(source=user, destination=sink, amount=Decimal("100"), kind="RIDE_PAYMENT", reference="ride-2")


@pytest.mark.django_db
def test_posting_same_reference_is_idempotent():
    funder = Account.objects.create(kind="SYSTEM", code="sys:f2", allow_negative=True)
    user = Account.objects.create(kind="USER", code="user:2")
    first = transfer(source=funder, destination=user, amount=Decimal("10"), kind="TOPUP", reference="dup-1")
    second = transfer(source=funder, destination=user, amount=Decimal("10"), kind="TOPUP", reference="dup-1")
    assert first.pk == second.pk
```

---

## 12. Next steps

1. Run `make up`, then `make migrate`, and create an admin with `docker compose exec api python manage.py createsuperuser`.
2. Add `apps/rides`, `apps/matching`, and `apps/vehicles` next, since bookings depend on rides.
3. Replace the placeholder `ChatConsumer` with membership checks and message persistence.
4. Add `ImmutableQuerySet` to the `LedgerEntry` and `AuditLog` managers before going to production.
5. Put the Tunisian personal data rules from Law No. 2004-63 into `docs/legal/` before storing real CIN images.
