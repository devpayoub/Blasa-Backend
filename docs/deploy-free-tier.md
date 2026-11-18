# Free-tier hosting for the Blasa API (test environment)

Everything below is **$0, no credit card** except where noted. Verified November 2026.

## TL;DR — the stack

| Need | Service | Free allowance | Why this one |
| --- | --- | --- | --- |
| **API container** | **Render** Free Web Service (Docker) | 512 MB RAM / 0.1 CPU, 750 instance-hrs/mo, sleeps after 15 min idle | You already chose it. Works, but see the traps. |
| **Postgres + PostGIS** | **Neon** Free | 1 GB storage, 100 CU-hrs/mo **per project**, 100 projects, scale-to-zero after 5 min | **Do not use Render's free Postgres** — it is deleted 30 days after creation. |
| **Redis** (Channels + Celery broker) | **Upstash** Free *or* **Redis Cloud** Free | Upstash: 256 MB, 500K commands/mo. Redis Cloud: 30 MB, 30 connections | Render has **no free Key Value tier** (starts at $10/mo). |
| **File storage** (CIN / carte grise) | **Cloudflare R2** Free | 10 GB-month, 1M Class A + 10M Class B ops, **zero egress fees** | S3-compatible, so the existing boto3 presign code works unchanged. |
| **Email** | **Brevo** Free | 300 emails/day | Must be an **HTTP API**, not SMTP — Render free blocks outbound SMTP ports. |
| **SMS (OTP)** | **Brevo** Free | 300 SMS credits | Same API key as email; `apps/auth/sms.py` dispatches through it. |
| **Push** | **Firebase FCM** | free, unlimited | Already wired via `firebase-admin`. |
| **Error tracking** | **Sentry** Free | 5,000 errors/mo | `prod.py` already reads `SENTRY_DSN`. |

**Total: $0.** The only thing you cannot get for free is an always-on instance and a background worker — both covered under "What won't work" below.

---

## 1. Why not Render's own free database and Redis

Two hard blockers, both documented by Render:

- **Free Postgres is deleted.** 1 GB, one per workspace, **expires 30 days after creation**, then a 14-day grace period, then permanent deletion. No backups. You will lose your test data mid-sprint.
- **No free Key Value (Redis).** Cheapest is $10/mo for 256 MB. `CHANNEL_LAYERS` and `CELERY_BROKER_URL` both point at Redis, so the app will not even boot cleanly without one.
- **No free background worker.** Workers start at $7/mo. `celery` has no free home on Render.

Render's free Postgres *does* support PostGIS (`CREATE EXTENSION postgis` works on PG 13+), so it is usable for a throwaway 2-week demo. It is just not usable as a test environment you come back to.

---

## 2. Service setup, in order

### 2.1 Neon — Postgres with PostGIS

1. Sign up at `neon.com`, create a project. Pick the region closest to Frankfurt (or Frankfurt itself) to keep latency to Render low.
2. Open the **SQL Editor** and run:
   ```sql
   CREATE EXTENSION IF NOT EXISTS postgis;
   ```
   Without this, every migration touching `Ride.origin` / `Ride.destination` fails.
3. Copy the **pooled** connection string from the dashboard and append `?sslmode=require`:
   ```
   postgresql://user:pass@ep-xxx-pooler.eu-central-1.aws.neon.tech/neondb?sslmode=require
   ```
   `django-environ` parses this fine, and `base.py:79` overrides the engine to `django.contrib.gis.db.backends.postgis` regardless of the scheme.

Notes: the compute suspends after 5 minutes idle, so the first request after a quiet spell pays a ~1 s wake-up on top of Render's own cold start. 100 CU-hours/month is plenty for testing.

**Alternatives:** Supabase Free (500 MB, but projects **pause after 7 days of inactivity** and need a manual resume — worse for testing). Aiven Free Postgres is 1 GB but PostGIS availability varies by plan.

### 2.2 Upstash — Redis

1. Create a Redis database at `upstash.com`, free plan, same region as Render.
2. Copy the **TLS** endpoint. It must use `rediss://`, not `redis://`:
   ```
   rediss://default:<password>@<host>.upstash.io:6379
   ```
   `channels_redis` and Celery both accept this URL as-is.

**Alternative:** Redis Cloud Free (30 MB, 30 connections) gives a conventional TCP Redis, which is a better fit for `channels_redis` pub/sub than Upstash's serverless model. Either works for testing; pick Redis Cloud if websocket fan-out misbehaves.

### 2.3 Cloudflare R2 — private document bucket

1. Create a bucket, e.g. `blasa-private-docs`. **Do not enable public access** — these are identity documents.
2. Create an API token with Object Read & Write on that bucket. Note the Access Key ID and Secret.
3. Your endpoint is `https://<account-id>.r2.cloudflarestorage.com`.

Env vars:
```
S3_BUCKET_PRIVATE=blasa-private-docs
S3_ENDPOINT_URL=https://<account-id>.r2.cloudflarestorage.com
S3_REGION=auto
AWS_ACCESS_KEY_ID=<token id>
AWS_SECRET_ACCESS_KEY=<token secret>
```

`S3_REGION` was missing from `settings/base.py` even though `verification/storage.py:9` reads it — boto3 cannot sign a presigned URL without a region. It has been added; R2 and B2 both want `auto`.

### 2.4 Brevo — email and SMS over HTTPS

Render's free tier blocks outbound SMTP ports, so `EMAIL_HOST` + `send_mail` would
time out. The backend now ships a Brevo HTTP adapter:

- `core/delivery/brevo.py` implements `BrevoEmailBackend` (a Django email backend)
  and `send_transactional_sms`.
- Setting `BREVO_API_KEY` switches `EMAIL_BACKEND` to that adapter and sets
  `SMS_PROVIDER=brevo` automatically. `apps/auth/sms.py` dispatches through it.
- Failures are logged by class name only (never the payload, which carries OTP
  codes) and return "not sent" instead of raising, so a provider outage cannot
  break login.

Setup:

1. Create a Brevo account and verify a sender domain (or a single sender email).
2. Create an API key under **SMTP & API → API keys** with email + SMS permissions.
3. Set:
   ```
   BREVO_API_KEY=xkeysib-...
   DEFAULT_FROM_EMAIL=Blasa <no-reply@yourdomain>
   BREVO_SMS_SENDER=Blasa
   ```
4. Brevo's free plan is 300 emails/day and 300 SMS credits. SMS sender names
   must be pre-approved in the Brevo dashboard; Tunisia delivery is best-effort.

With no API key the adapter is bypassed and OTP delivery stays a log-only no-op
(the code is never written to logs, by design).

### 2.5 Sentry — error monitoring

`core/observability.py` initialises `sentry-sdk` from `SENTRY_DSN` for web,
Celery and Redis. `before_send` strips `Authorization`, `api-key`, `password`,
`token`, `code` and `otp` keys before an event leaves the process.

```
SENTRY_DSN=https://<key>@o<org>.ingest.sentry.io/<project>
SENTRY_ENVIRONMENT=production
SENTRY_TRACES_RATE=0.05
```

Free plan: 5,000 errors/month. Without a DSN nothing is initialised and no
network call is attempted.

### 2.6 Upstash TLS details

Use the `rediss://` endpoint. `settings/base.py` detects the scheme and passes
`{"address": REDIS_URL, "ssl_cert_reqs": None}` to `channels_redis`, which is
required for Upstash's certificate chain. Celery reads the same URL, and
`CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP` is enabled so an idle-dropped
connection retries instead of crashing the worker on boot.

---

## 3. Deploy

The backend is **not a git repository yet** (only `frontend/` has `.git`). Render deploys from git, so:

```bash
cd backend
git init
git add .
git commit -m "Initial backend"
git remote add origin <your-repo-url>
git push -u origin main
```

Then in Render: **New → Blueprint**, select the repo. It reads `render.yaml` at the repo root and prompts for the `sync: false` values.

Files added for this:
- `render.yaml` — the blueprint (web service only, no Render DB/Redis)
- `infra/docker/api.render.Dockerfile` — prod image, installs `requirements/prod.txt`, runs non-root
- `config/urls.py` — added `/healthz` for Render's health check
- `settings/base.py` — added `whitenoise` middleware, `S3_REGION`, and made `CELERY_TASK_ALWAYS_EAGER` env-driven
- `requirements/base.txt` — added `whitenoise`
- `core/delivery/brevo.py` — Brevo email backend + transactional SMS adapter
- `core/observability.py` — Sentry init with credential scrubbing
- `apps/auth/sms.py` — provider switch (`console` | `brevo`)
- `apps/auth/views.py` — contact check, password login, registration completion

### Why Docker and not Render's native Python runtime

GeoDjango needs GDAL, GEOS and PROJ at runtime. `django.contrib.gis` will not import without them, and Render's native Python runtime gives you no `apt-get`. Your existing `api.Dockerfile` already installs `gdal-bin libgdal-dev libproj-dev` — that is why the Docker path is mandatory, not a preference.

---

## 4. Traps specific to this codebase

These will each cost you an hour if you hit them cold:

| Trap | Detail |
| --- | --- |
| **Wrong settings module** | `manage.py:3`, `celery.py:5` and `asgi.py:6` all default to `config.settings.dev`. If `DJANGO_SETTINGS_MODULE` is not set to `config.settings.prod`, you deploy with `DEBUG=True` and the dev database. |
| **Boot crash without hosts** | `prod.py:35` raises `RuntimeError` at import if `DJANGO_ALLOWED_HOSTS` is empty. Set it to `blasa-api.onrender.com`. |
| **512 MB is not much** | `--workers 4` from the compose file will be OOM-killed. The blueprint uses `--workers 1`. |
| **Celery never runs** | No free worker. `CELERY_TASK_ALWAYS_EAGER=1` makes `.delay()` execute inline. Without it, `send_otp_sms.delay()` queues a message nobody consumes — and login silently breaks. |
| **Identity uploads are off** | `prod.py:34` defaults `IDENTITY_DOCUMENT_UPLOADS_ENABLED` to `False`. `POST /verification/documents/upload-url/` returns 403. Expected — flip it only in a throwaway environment. |
| **Admin CSS** | Django admin is the *only* place verification documents can be approved (`verification/admin.py`). Without static file serving it renders unstyled. `whitenoise` was added for this. |
| **Flouci webhook needs a public URL** | Point it at `https://blasa-api.onrender.com/api/v1/payments/webhooks/flouci/`. The handler verifies HMAC-SHA256 against `FLOUCI_WEBHOOK_SECRET`, so the default `dev-flouci-webhook-secret` must be changed or webhooks will be rejected. |
| **Cold starts** | Free services sleep after 15 min. The first request takes ~60 s, and the Expo app's 15 s client timeout will fire first. Expect a failed first request after idle. |
| **750 instance-hours/month** | Shared across the workspace. One always-on service burns ~730 hrs, so you have essentially one service. |

---

## 5. What genuinely will not work on free tier

- **WebSockets on a sleeping service.** Chat works once the instance is awake, but every cold start drops all sockets.
- **Celery beat / scheduled jobs.** No beat scheduler, no free worker, and no `django-celery-beat` in the repo. Ride reminders (already missing per the audit) cannot be scheduled here.
- **Anything latency-sensitive.** 0.1 CPU shared with a GDAL-importing Django process.
- **Real document storage compliance.** R2 is fine technically, but PRD §13 and `docs/legal/law-2004-63-compliance.md` gate real CIN uploads on legal sign-off, not on infrastructure.

## 6. When you outgrow it

The cheapest path off free tier is not "more Render": it is **Render Starter at $7/mo**, which removes the spin-down, plus keeping Neon and Upstash free. That gets you an always-on API for $7 with no data-expiry risk. Add a $7 worker only when you need real Celery.
