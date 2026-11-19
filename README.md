# Blasa — Backend

Modular-monolith Django + DRF API for **Blasa**, a Tunisian ride-sharing platform
(passengers, drivers, vehicle verification, trips, bookings, wallet, brand portal).

## Stack

| Concern | Choice |
| --- | --- |
| Framework | Django 5 + Django REST Framework |
| Database | PostgreSQL 16 + PostGIS |
| Async tasks | Celery + Celery Beat (Redis broker) |
| Realtime | Django Channels (Redis channel layer) |
| Auth | SimpleJWT + OTP (SMS / email) |
| API contract | drf-spectacular → `apps/api/generated/schema.yaml` → typed TS client |
| Storage | S3-compatible (Cloudflare R2 in production) |
| Tests / lint | pytest + ruff |

## Layout

```
apps/api/
  api/            thin HTTP layer (URLs, views, serializers, permissions)
  apps/           domain apps: users, drivers, vehicles, trips, bookings,
                  payments, wallet, chat, notifications, brand, moderation, ...
  core/           shared infrastructure: exceptions, delivery (Brevo), observability (Sentry)
  config/         settings (base / dev / test / prod), celery, asgi
  tests/          pytest suite
infra/docker/     local Docker Compose stack + production Dockerfiles
docs/             architecture notes, free-tier deploy guide, legal, PRDs
```

## Local development

Requires Docker Desktop.

```bash
cp .env.example .env          # then fill in DATABASE_URL / REDIS_URL if not using Compose
make up                       # build + start db, redis, api, worker, beat
make migrate
make test                     # pytest
make lint                     # ruff
```

The API is served at <http://localhost:8000>. Health probe: `/healthz`.
OpenAPI schema: `/api/schema/`, Swagger UI: `/api/docs/`. All routes live under
`/api/v1/`.

Regenerate the shared TypeScript client after any API change:

```bash
docker compose -f infra/docker/docker-compose.yml exec api \
  python scripts/generate_shared_types.py
```

This writes `apps/api/generated/schema.yaml` and `apps/api/generated/api.ts`; copy
`api.ts` into the frontend's `packages/shared-types/` (or consume it from there).

## Error contract

Every failure returns the same envelope, so clients never have to guess:

```json
{ "error": { "status": 400, "code": "VALIDATION_ERROR", "detail": "…" } }
```

Implemented in `apps/api/core/exceptions.py`. Status → code mapping lives in
`CODE_BY_STATUS`; add a new code there rather than inventing ad-hoc shapes.

## Authentication flows

1. `POST /api/v1/auth/contact/check/` — does this email/phone exist, and does it have a password?
2. Existing user with password → `POST /api/v1/auth/password/login/`
3. Otherwise, email (the primary path) → `POST /api/v1/auth/email/request/` → `POST /api/v1/auth/email/verify/`
   (6-digit code; verifying a new address creates the account with `phone_number = NULL`)
4. Phone instead → `POST /api/v1/auth/otp/request/` → `POST /api/v1/auth/otp/verify/`
5. New user → `POST /api/v1/auth/register/complete/` (full name, password, role)
6. `POST /api/v1/verification/documents/` — drivers upload CIN + carte grise

Password is set exactly once (`has_usable_password()` guard). A `DRIVER` role does
**not** bypass document verification, and public profiles never expose contact or
wallet fields.

## Deployment (free tier)

Fully documented in [`docs/deploy-free-tier.md`](docs/deploy-free-tier.md).

| Service | Provider |
| --- | --- |
| API + worker | Render (`render.yaml`) |
| Database | Neon (serverless Postgres) |
| Email + SMS | Brevo (HTTPS API — Render free blocks SMTP) |
| Object storage | Cloudflare R2 |
| Redis | Upstash (`rediss://`, TLS) |
| Error monitoring | Sentry |

All provider credentials are read from environment variables; see `.env.example`
for the full list. Nothing is hardcoded.

## Tests

```bash
make test
```

The suite covers auth/onboarding, verification gating, trip lifecycle, bookings,
wallet invariants, chat permissions, notifications, and the brand portal.

### Live end-to-end check (real database, real mail)

`pytest` runs against the test settings with an empty database and a locmem mail
backend, so it can never prove the real wiring works. `scripts/e2e_email_auth.py`
does: it drives a **running** API over HTTP using the real `.env` — real
database, real JWT signing, real Brevo delivery — and walks the whole email-only
journey (check → code → verify → register → password login → refresh → logout).

```bash
make e2e                      # fully automated: seeds a known code, sends no mail
make e2e-email EMAIL=you+e2e@gmail.com   # sends a real Brevo email, prompts for the code
```

`make e2e` needs Django, so it runs inside the `api` container. The email mode
works with the standard library alone, so it can also point at the deployed API:

```bash
python apps/api/scripts/e2e_email_auth.py --mode email \
  --base-url https://blasa-backend.onrender.com --email you+e2e@gmail.com
```

Every check prints PASS/FAIL and the exit code is non-zero if anything fails.
