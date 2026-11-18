# Blasa

Ride-sharing and carpooling platform for Tunisia (Tunis, Sousse).
Backend: Django + Django REST Framework (modular monolith).
Mobile: React Native (Expo). Admin and brand dashboards: React (Vite).

---

## Repository layout

```text
blasa/
├── apps/
│   ├── api/                          # Django backend (modular monolith)
│   ├── mobile/                       # React Native + Expo (driver & passenger apps)
│   │   ├── driver/
│   │   └── passenger/
│   └── admin/                        # React (Vite) admin console and brand dashboard
├── packages/
│   ├── shared-types/                 # TypeScript types generated from the OpenAPI schema
│   ├── validation/                   # Shared validation schemas (Zod)
│   └── ui/                           # Shared React UI components
├── infra/
│   ├── docker/
│   │   ├── api.Dockerfile
│   │   ├── admin.Dockerfile
│   │   └── docker-compose.yml        # api, postgres+postgis, redis, worker, beat
│   ├── terraform/
│   │   ├── modules/
│   │   ├── environments/
│   │   │   ├── staging/
│   │   │   └── production/
│   │   └── main.tf
│   ├── k8s/                          # Optional, for later scaling
│   └── scripts/
├── docs/
│   ├── prd/                          # Product requirements
│   ├── architecture/
│   │   ├── adr/                      # Architecture Decision Records
│   │   ├── booking-state-machine.md
│   │   └── wallet-ledger.md
│   ├── legal/
│   │   ├── law-2004-63-compliance.md # Personal data (CIN, profiles)
│   │   ├── wallet-regulation.md      # Central bank questions
│   │   └── sponsorship-accounting.md
│   └── api/                          # Generated OpenAPI docs
├── .github/
│   └── workflows/                    # CI: lint, test, build, deploy
├── .env.example
├── .gitignore
├── Makefile
└── README.md
```

---

## Backend (`apps/api`)

```text
apps/api/
├── manage.py
├── pyproject.toml                    # Dependencies (Poetry or uv)
├── requirements/
│   ├── base.txt
│   ├── dev.txt
│   └── prod.txt
├── pytest.ini
├── config/                           # Project settings and entry points
│   ├── __init__.py
│   ├── asgi.py                       # Channels / Socket.IO-compatible ASGI entry
│   ├── wsgi.py
│   ├── urls.py                       # Root URL router, mounts /api/v1/
│   ├── celery.py                     # Celery app (or RQ, if preferred)
│   └── settings/
│       ├── __init__.py
│       ├── base.py
│       ├── dev.py
│       ├── test.py
│       └── prod.py
├── core/                             # Shared kernel: no business logic
│   ├── __init__.py
│   ├── models.py                     # TimeStampedModel, UUID base model
│   ├── permissions.py
│   ├── pagination.py
│   ├── exceptions.py
│   ├── middleware.py                 # Request ID, locale (ar/fr/en)
│   ├── geo.py                        # Distance and bounding-box helpers (PostGIS)
│   ├── storage.py                    # Private S3 client, signed URLs
│   ├── events.py                     # Domain event bus (in-process)
│   └── utils.py
├── apps/                             # Domain modules (Django apps)
│   ├── __init__.py
│   │
│   ├── auth/                         # Login, OTP via SMS, JWT, sessions
│   │   ├── __init__.py
│   │   ├── apps.py
│   │   ├── services.py
│   │   ├── otp.py
│   │   ├── tokens.py
│   │   ├── serializers.py
│   │   ├── views.py
│   │   ├── urls.py
│   │   └── tests/
│   │
│   ├── users/                        # Profiles, roles (passenger, driver, admin)
│   │   ├── __init__.py
│   │   ├── apps.py
│   │   ├── models.py                 # User (custom AUTH_USER_MODEL), Profile
│   │   ├── services.py
│   │   ├── selectors.py
│   │   ├── serializers.py
│   │   ├── views.py
│   │   ├── urls.py
│   │   ├── admin.py
│   │   ├── migrations/
│   │   └── tests/
│   │
│   ├── verification/                 # CIN and Carte Grise (isolated, sensitive)
│   │   ├── __init__.py
│   │   ├── apps.py
│   │   ├── models.py                 # IdentityDocument, VehicleDocument, ReviewDecision
│   │   ├── services.py
│   │   ├── selectors.py
│   │   ├── storage.py                # Encrypted uploads, access via signed URLs only
│   │   ├── serializers.py
│   │   ├── views.py
│   │   ├── urls.py
│   │   ├── admin.py
│   │   ├── migrations/
│   │   └── tests/
│   │
│   ├── vehicles/                     # Cars linked to drivers
│   │   ├── models.py
│   │   ├── services.py
│   │   ├── serializers.py
│   │   ├── views.py
│   │   ├── urls.py
│   │   ├── migrations/
│   │   └── tests/
│   │
│   ├── rides/                        # Ride offers (driver) and route geometry
│   │   ├── __init__.py
│   │   ├── apps.py
│   │   ├── models.py                 # Ride, RideStop, RouteGeometry (PostGIS)
│   │   ├── state.py                  # Ride lifecycle states
│   │   ├── services.py
│   │   ├── selectors.py
│   │   ├── serializers.py
│   │   ├── views.py
│   │   ├── urls.py
│   │   ├── admin.py
│   │   ├── migrations/
│   │   └── tests/
│   │
│   ├── bookings/                     # Booking state machine
│   │   ├── __init__.py
│   │   ├── apps.py
│   │   ├── models.py                 # Booking, BookingEvent
│   │   ├── state_machine.py          # REQUESTED → ACCEPTED → CONFIRMED → COMPLETED
│   │   ├── services.py
│   │   ├── selectors.py
│   │   ├── serializers.py
│   │   ├── views.py
│   │   ├── urls.py
│   │   ├── admin.py
│   │   ├── migrations/
│   │   └── tests/
│   │
│   ├── matching/                     # Geo-based matching (PostGIS queries)
│   │   ├── __init__.py
│   │   ├── services.py               # Scoring: distance, detour, time window
│   │   ├── selectors.py              # ST_DWithin, bounding boxes
│   │   ├── scoring.py
│   │   ├── tasks.py                  # Background matching jobs
│   │   └── tests/
│   │
│   ├── interests/                    # Passenger interest in future rides
│   │   ├── models.py
│   │   ├── services.py
│   │   ├── views.py
│   │   ├── urls.py
│   │   ├── migrations/
│   │   └── tests/
│   │
│   ├── wallet/                       # Double-entry ledger (immutable)
│   │   ├── __init__.py
│   │   ├── apps.py
│   │   ├── models.py                 # Account, LedgerEntry, Transaction (append-only)
│   │   ├── services.py               # post_transaction(), reserve(), release()
│   │   ├── selectors.py              # balance computed from entries
│   │   ├── constraints.py            # Debits equal credits, no updates or deletes
│   │   ├── serializers.py
│   │   ├── views.py
│   │   ├── urls.py
│   │   ├── admin.py                  # Read-only admin views
│   │   ├── migrations/
│   │   └── tests/
│   │
│   ├── payments/                     # Gateway integrations (Flouci, cards, cash)
│   │   ├── __init__.py
│   │   ├── models.py                 # PaymentIntent, WebhookEvent
│   │   ├── gateways/
│   │   │   ├── base.py               # Abstract gateway interface
│   │   │   ├── flouci.py
│   │   │   └── cash.py
│   │   ├── services.py
│   │   ├── webhooks.py               # Signature checks, idempotency keys
│   │   ├── views.py
│   │   ├── urls.py
│   │   ├── migrations/
│   │   └── tests/
│   │
│   ├── incentives/                   # Fuel vouchers and promotional credits
│   │   ├── models.py
│   │   ├── services.py
│   │   ├── migrations/
│   │   └── tests/
│   │
│   ├── brands/                       # Sponsors, campaigns, sponsored rides
│   │   ├── models.py                 # Brand, Campaign, SponsoredRide
│   │   ├── services.py
│   │   ├── selectors.py
│   │   ├── serializers.py
│   │   ├── views.py
│   │   ├── urls.py
│   │   ├── admin.py
│   │   ├── migrations/
│   │   └── tests/
│   │
│   ├── ratings/                      # Two-way ratings and reviews
│   │   ├── models.py
│   │   ├── services.py
│   │   ├── views.py
│   │   ├── urls.py
│   │   ├── migrations/
│   │   └── tests/
│   │
│   ├── messaging/                    # Chat between matched users
│   │   ├── __init__.py
│   │   ├── models.py                 # Conversation, Message
│   │   ├── consumers.py              # WebSocket consumers (Channels)
│   │   ├── routing.py
│   │   ├── services.py
│   │   ├── views.py
│   │   ├── urls.py
│   │   ├── migrations/
│   │   └── tests/
│   │
│   ├── notifications/                # FCM push, SMS, in-app
│   │   ├── __init__.py
│   │   ├── models.py                 # Notification, DeviceToken
│   │   ├── channels/
│   │   │   ├── push_fcm.py
│   │   │   ├── sms.py
│   │   │   └── in_app.py
│   │   ├── services.py
│   │   ├── tasks.py
│   │   ├── templates/                # ar / fr / en message templates
│   │   ├── views.py
│   │   ├── urls.py
│   │   ├── migrations/
│   │   └── tests/
│   │
│   ├── moderation/                   # Reports, bans, content review
│   │   ├── models.py
│   │   ├── services.py
│   │   ├── views.py
│   │   ├── urls.py
│   │   ├── migrations/
│   │   └── tests/
│   │
│   ├── reporting/                    # Analytics and exports for admins
│   │   ├── selectors.py
│   │   ├── services.py
│   │   ├── views.py
│   │   ├── urls.py
│   │   └── tests/
│   │
│   ├── audit/                        # Immutable log of admin actions
│   │   ├── models.py                 # AuditLog (append-only)
│   │   ├── services.py
│   │   ├── middleware.py
│   │   ├── migrations/
│   │   └── tests/
│   │
│   └── admin/                        # Custom admin console extensions (Django admin)
│       ├── __init__.py
│       ├── apps.py
│       ├── site.py                   # Custom AdminSite, RBAC roles
│       ├── permissions.py
│       ├── views.py
│       ├── urls.py
│       └── tests/
│
├── api/                              # Versioned API routing
│   ├── __init__.py
│   ├── v1/
│   │   ├── __init__.py
│   │   ├── urls.py                   # Includes every app's urls.py under /api/v1/
│   │   └── schema.py                 # OpenAPI schema (drf-spectacular)
│
└── tests/                            # Cross-module integration and e2e tests
    ├── conftest.py
    ├── factories/
    ├── integration/
    │   ├── test_booking_flow.py
    │   ├── test_wallet_ledger.py
    │   └── test_matching.py
    └── fixtures/
```

---

## Key conventions

- **Layering:** Each app follows `models → selectors (reads) → services (writes) → views/serializers`. Views never touch models directly, and business rules live only in services.
- **Module boundaries:** Apps talk to each other through services or domain events in `core/events.py`, never by importing another app's models for writes.
- **Wallet:** Balances are derived from `LedgerEntry` rows. Entries are never updated or deleted, and each transaction must balance to zero.
- **Booking states:** `REQUESTED → ACCEPTED → CONFIRMED → COMPLETED`. Terminal states are `REJECTED`, `CANCELLED_BY_PASSENGER`, `CANCELLED_BY_DRIVER`, and `NO_SHOW`. Every transition writes a `BookingEvent` and an audit entry.
- **Sensitive documents:** CIN and Carte Grise files are stored encrypted in a private bucket and are served only through short-lived signed URLs.
- **API:** Versioned under `/api/v1/`, with OpenAPI generated by drf-spectacular. The mobile and admin apps generate their types from that schema.
- **Localization:** Arabic, French, and English, using Django i18n for the API and template files for notifications.

---

## Environment and infrastructure

| Component | Technology |
|---|---|
| Database | PostgreSQL with PostGIS (`django.contrib.gis`) |
| Cache and broker | Redis |
| Background jobs | Celery with Redis broker, or Django-Q2 |
| Real-time | Django Channels with Daphne or Uvicorn |
| Object storage | S3-compatible private bucket |
| Push notifications | Firebase Cloud Messaging |
| SMS | Local Tunisian provider or Twilio |
| Maps | Mapbox or Google Maps SDK |
