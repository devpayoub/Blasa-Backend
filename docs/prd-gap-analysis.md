# Blasa — PRD vs. Implementation Gap Analysis

**Source of truth:** `Blasa Dev Plan.md` (PRD v1.0)
**Audited:** `backend/apps/api` (Django + DRF + PostGIS) and `frontend/src` (Expo / expo-router)
**Date:** 2026-11-17

## Verdict at a glance

| Layer | Status |
| --- | --- |
| PRD → Backend | ~65% covered. Strong domain core (wallet ledger, booking state machine, Flouci, geo matching). Missing: email auth, ride editing, ride search filters, interest taxonomy, welcome bonuses, admin review REST API. |
| Backend → Frontend | ~25% consumed. The app only calls rides, bookings, conversations, interests, ratings, vehicles, users, wallet-read. |
| Frontend → PRD screens | ~40% of passenger app, ~25% of driver app, 0% of admin/brand. |
| **Blocking issue** | `EXPO_PUBLIC_MOCK=1` in `frontend/.env:6` — the app never calls the backend at all. |

---

## 0. The login example you raised — confirmed, and wider than expected

PRD §3A: *"Create an account using their phone number **and/or email**."*

| Where | Finding |
| --- | --- |
| `backend/.../apps/users/models.py:42` | `USERNAME_FIELD = "phone_number"`. Email is a blank optional field (`:29`), not a login identifier. |
| `backend/.../apps/auth/serializers.py:3` | OTP request accepts only `phone_number` matching `^\+216\d{8}$`. |
| `backend/.../apps/auth/views.py:14-51` | Only two auth endpoints: `otp/request`, `otp/verify`. No email/password, no email OTP. |
| `frontend/src/app/(auth)/phone.tsx:82-95` | Single input, `keyboardType="phone-pad"`. Copy at `:60` says *"No password — just your phone number."* |
| `frontend/src` (whole tree) | `grep -rniE "email|mail"` → **zero matches**. |

**Verdict: phone-only in both layers. Email login does not exist anywhere.**

Related gaps in the same section that make registration thinner than the PRD implies:

- **No registration screen.** Account creation is implicit on first OTP verify (`phone.tsx:106-108`).
- **No personal-information step.** PRD §3A/§3B require it. `Profile` exists in the backend but `UserSerializer.profile` is declared `read_only=True` (`users/serializers.py:14`) — so `PATCH /users/me/` **cannot** write `full_name` or `bio`. Only `preferred_language` is writable. This is a real bug, not just a missing screen.
- **No email capture at all**, even as a profile field, despite the model supporting it.

---

## 1. Section-by-section gap table

Legend: ✅ implemented · ⚠️ partial · ❌ missing

### §3 Registration & identity verification

| Requirement | Backend | Frontend |
| --- | --- | --- |
| Account via phone **and/or email** | ⚠️ phone only | ⚠️ phone only |
| Enter personal information | ❌ serializer read-only | ❌ no screen |
| Upload CIN photo | ⚠️ API exists, gated off | ❌ no UI |
| Upload carte grise | ⚠️ via vehicle FK | ❌ never sent |
| Submit for verification | ✅ | ❌ |
| Admin approve/reject | ⚠️ Django admin only | ❌ |
| Admin request more documents | ❌ | ❌ |
| Admin suspend/deactivate | ⚠️ separate `moderation` ban | ❌ |
| Secure document storage | ✅ private S3 presigned URLs | — |

Notes:
- `verification/views.py:40-46` returns **403** unless `IDENTITY_DOCUMENT_UPLOADS_ENABLED` is true. Prod default is `False` (`config/settings/prod.py:34`), held back for Law 2004-63 sign-off.
- `verification/services.review_document()` exists but is reachable **only** from Django admin (`verification/admin.py:15-23`). There is no `POST /verification/documents/{id}/review/` endpoint, so no admin dashboard can ever be built on it.
- `frontend/src/services/verification.ts` is fully written and **imported by nothing**. The only CIN entry point is a dead button that fires an alert (`account.tsx:153-160`).

### §4 Ride management & booking

| Requirement | Backend | Frontend |
| --- | --- | --- |
| Publish a ride | ✅ | ✅ `ride/publish.tsx` |
| Departure + destination | ✅ PostGIS | ✅ map point picker |
| Date and time | ✅ | ⚠️ plain text `YYYY-MM-DD HH:MM` |
| Available seats | ✅ | ✅ |
| Contribution per passenger | ✅ | ✅ |
| Ride details / **notes** | ❌ **no field on `Ride`** | ❌ |
| Preferred passenger interests | ❌ | ❌ |
| Accept / reject booking requests | ✅ | ✅ |
| View passenger profile + booking info | ⚠️ no public profile endpoint | ⚠️ |
| **Edit a published ride** | ❌ no Update mixin | ❌ |
| **Cancel a published ride** | ❌ `CANCELLED` never assigned | ❌ |
| Track upcoming / completed | ⚠️ `COMPLETED`/`IN_PROGRESS` never assigned | ⚠️ |
| Search by departure **and** destination | ❌ no filters on `RideViewSet` | ❌ no search UI |
| Select date/time | ❌ | ❌ |
| Compare ride details, prices, profiles | ❌ | ❌ |
| Book a ride | ✅ | ✅ |
| View booking status | ✅ | ✅ |
| Cancel per cancellation policy | ⚠️ no time-based policy | ✅ |
| Rate driver after ride | ✅ | ✅ 1–5, no comment |
| Interactive map | ⚠️ radius query only | ⚠️ point picker only |
| Filters: date / time / price / seats | ❌ | ❌ |
| Suggested rides by route compatibility | ✅ `matching` app | ✅ `interests.tsx` |
| Notify when matching ride appears | ✅ in-app | ⚠️ no notifications screen |

Critical backend detail — `rides/views.py:12`:
```python
class RideViewSet(mixins.CreateModelMixin, mixins.RetrieveModelMixin, mixins.ListModelMixin, ...)
```
No `UpdateModelMixin`, no `DestroyModelMixin`, no `filterset_fields`. So:
- A driver **cannot edit or cancel** a ride through the API.
- `GET /rides/` returns **every ride in the database** — there is no way to search by origin/destination, date, price or seats. The only search is `GET /rides/near/?lat&lng&radius_km`, which filters on **origin only**, ignoring destination entirely.

Cancellation/refund asymmetry (`bookings/services.py:81-90`): a refund is issued only when `action == "cancel_driver"` **and** the booking was `CONFIRMED`. A passenger cancelling a confirmed booking releases the seats but **receives no refund**.

### §5 Interest-based matching — the differentiator, essentially absent

This is the largest conceptual gap in the product.

| Requirement | Status |
| --- | --- |
| Select/edit interest categories on profile | ❌ |
| PRD categories (music, tech, entrepreneurship, sports, culture, politics, hobbies) | ❌ |
| Display shared interests driver ↔ passenger | ❌ |
| Optional filter by common interests | ❌ |
| Show count of interests in common | ❌ |
| Preferred ride atmosphere (music / conversation / quiet) | ❌ |

There is a **naming collision** that hides this gap. `apps/interests/models.py:8` defines:

```python
class Interest(TimeStampedModel):
    """A passenger's standing request for a future route."""
```

`Interest` is a **saved route request** (origin, destination, departure window, seats) — it is not a user interest tag. Grep for `music|genre|playlist|football|cinema|hobby|atmosphere|quiet` across the backend returns **zero hits**.

The matching engine (`apps/matching/scoring.py:6-8`) scores route only:

```
score = 100 * (0.4 * pickup_dev + 0.3 * dropoff_dev + 0.3 * time_score)
```

There is no interest term at all, so the PRD's *"route compatibility first, shared interests as an additional criterion"* is not implementable on the current model.

### §6 Wallet & payments

| Requirement | Backend | Frontend |
| --- | --- | --- |
| View balance | ✅ | ✅ account card |
| Add funds | ✅ `POST /payments/topup/` | ❌ no UI |
| Transaction history | ✅ | ⚠️ last 5 only |
| Promotional credits | ⚠️ vouchers, no promo credit ledger | ❌ |
| Pay with wallet balance | ✅ at booking confirm | ⚠️ implicit |
| Refunds | ⚠️ driver-cancel only | — |
| **Flouci** | ✅ **real HTTP + HMAC webhook** | ❌ |
| Bank / direct payment | ❌ only `cash` + `flouci` gateways | ❌ |
| **10 TND passenger welcome credit** | ❌ | ❌ |
| **10 TND driver fuel voucher** | ❌ | ❌ |
| Admin campaign & eligibility management | ❌ | ❌ |

Good news: the wallet core is genuinely solid. `wallet/services.py:46` enforces `sum(legs) == 0` (true double-entry), idempotency by `reference` (`:49-51`), ordered row locks (`:54-55`), overdraft checks (`:59-60`), and `LedgerEntry.save/delete` are immutable (`models.py:46-52`). Flouci is real (`gateways/flouci.py:34-44`, `urllib` POST with Bearer key, 15 s timeout; HMAC-SHA256 verify at `:59-61`).

But `grep -riE "welcome|bonus|fuel|essence|mazout"` across all backend apps → **zero hits**. Both acquisition incentives from PRD §6 are entirely unimplemented, and there is no `Campaign`/eligibility model in `incentives` — only a manually issued `Voucher`.

### §7 Brand partnerships & advertising

| Requirement | Status |
| --- | --- |
| Brands create/manage campaigns | ⚠️ backend, **`IsAdminUser` only** |
| Sponsored rides | ✅ backend |
| In-ride product promotion | ❌ |
| Target audience by interests | ❌ no audience field on `Campaign` |
| Campaign analytics (impressions / engagement / redemptions) | ❌ only `spent` / `balance` |
| Promo code tracking | ❌ `Voucher` is not linked to `Campaign` |
| Brand management dashboard | ❌ |
| Brand role can actually log in and manage | ❌ `User.Role.BRAND` is **never referenced** by any permission class |

### §8 Ratings, reviews & trust

| Requirement | Status |
| --- | --- |
| Passengers rate drivers | ✅ |
| Drivers rate passengers | ✅ |
| Feedback only after completed rides | ✅ `ratings/services.py:18` |
| Average rating on profile | ⚠️ own profile only, no public endpoint |
| Completed rides count on profile | ❌ |
| Report inappropriate behaviour | ✅ |
| Admin moderation system | ✅ report review + ban/unban |

### §9 Notifications & communication

| Event | Triggers notification? |
| --- | --- |
| Account verification status | ❌ event published, **no subscriber** |
| Booking request received | ❌ event published, **no handler** |
| Booking accepted / rejected | ✅ |
| Ride reminder | ❌ no Celery beat, no reminder task |
| Ride cancellation | ✅ |
| Payment confirmation | ❌ |
| Wallet transaction | ❌ |
| Promotional offers | ❌ |
| New matching ride | ✅ in-app only |

Channels: in-app ✅, push ✅ (FCM, device registry exists), SMS ✅ code path but **no handler ever requests it**, **email ❌ entirely** (no `EMAIL_*` settings, no `send_mail`, no templates).

Messaging: realtime chat works (Channels + Redis, JWT over `?token=`, `consumers.py:47-95`). But a thread is created at booking `REQUESTED` (`bookings/services.py:61`) and `post_message` has **no status gate** — so users can message before a booking is confirmed, contrary to PRD §9.

### §10 Administrator dashboard

Backend APIs exist for reports (rides / revenue / cancellations / CSV export, all `IsAdminUser`) and moderation. But:

- There is **no admin dashboard UI anywhere** — no route, component, or service in the frontend.
- `rides` has no admin edit/investigate endpoint.
- `incentives` has no campaign management.
- `brands` is admin-only with no brand-facing surface.

### §11 Suggested application structure

| Passenger screen | Status | Driver screen | Status |
| --- | --- | --- | --- |
| Home & ride search | ⚠️ home yes, search no | My rides | ❌ |
| Explore rides | ⚠️ status chips only | Publish a ride | ✅ |
| My bookings | ✅ | Passenger requests | ⚠️ bookings tab |
| Interest matching | ⚠️ route matching, not interests | Ride schedule | ❌ |
| My wallet | ⚠️ card only, no screen | Earnings & incentives | ❌ |
| Messages | ✅ | Driver profile | ❌ |
| My profile | ⚠️ account tab, no edit | | |
| Notifications | ❌ | | |

**Brand & admin portals: absent.**

---

## 2. The blocker you will hit first: the app is 100% mock

`frontend/.env:6` → `EXPO_PUBLIC_MOCK=1`
`frontend/src/api/client.ts:100-106` → `request()` returns fixtures **before any network call**
`frontend/src/lib/chatSocket.ts:18` → no WebSocket is opened in mock mode

So every screen you click today is served by `src/api/mock.ts` in-memory fixtures. The service layer is written correctly and would work against the API, but it is currently bypassed. Setting `EXPO_PUBLIC_MOCK=0` is a one-line change that will immediately surface the real gaps (notably: no ride search filters, no notes field, no edit/cancel).

---

## 3. Backend bugs found during the audit

1. **`PATCH /users/me/` cannot set `full_name` or `bio`.** `UserSerializer.profile` is `read_only=True` (`users/serializers.py:14`). Profile data is therefore unsettable through the API — which also blocks the driver-verification name match.
2. **`DELETE /interests/{id}/` returns 405.** `InterestViewSet.destroy()` is defined (`interests/views.py:31-37`) but `DestroyModelMixin` is missing from the class bases (`:12-14`). Dead code; users cannot cancel a saved route. The frontend already calls `DELETE` (`services/interests.ts:39`).
3. **`GET /rides/` is unfiltered** and returns every ride platform-wide. No pagination filter, no ownership scoping, no route search.
4. **No ride lifecycle.** `Ride.Status.IN_PROGRESS`, `COMPLETED`, `CANCELLED` are declared (`rides/models.py:14`) but **never assigned anywhere** in the codebase. Rides stay `OPEN`/`FULL` forever, so "track upcoming and completed rides" (§4A) and ride reminders (§9) have no foundation.
5. **Passenger cancellation never refunds.** Only `cancel_driver` from `CONFIRMED` refunds (`bookings/services.py:87-90`).
6. **Verification review has no REST endpoint**, blocking any admin UI.
7. **`Report.target_id` is an unvalidated `CharField`** (`moderation/models.py:32`) — no FK integrity, so reports can point at nonexistent objects.
8. **`AuditLog` does not cover API traffic.** `AdminAuditMiddleware` only logs `/admin/` requests (`audit/middleware.py:14-18`), and `AuditLog` is not registered in Django admin.

---

## 4. Recommended fix order

**Phase A — make the existing product real (highest value, lowest effort)**
1. Set `EXPO_PUBLIC_MOCK=0`; fix the breakage this reveals.
2. Add `UpdateModelMixin` + `DestroyModelMixin` to `RideViewSet`; implement `cancel_ride` releasing seats and refunding confirmed bookings.
3. Add `django-filter` to `RideViewSet`: origin/destination proximity, `departure_at` range, `price_per_seat` max, `seats_available__gte`.
4. Add `notes` to `Ride` + serializer + publish form.
5. Fix `UserSerializer.profile` to be writable; build a registration/profile screen (name, email, photo).
6. Wire `verification.ts` into the app behind the existing legal flag; add `POST /verification/documents/{id}/review/`.

**Phase B — close PRD §3 and §9**
7. Email as an alternate login (email OTP or email+password) + email notification channel.
8. Add the missing notification handlers: `verification.approved/rejected`, `booking.created`, `payment.succeeded`, wallet transactions.
9. Build the notifications screen.

**Phase C — the differentiator (§5)**
10. New `InterestTag` / `UserInterest` taxonomy + `RideAtmosphere` enum; migration.
11. Profile interest picker; shared-interest computation on ride detail; `?shared_interests=true` filter.
12. Add an interest term to `matching/scoring.py` **behind** route score (route stays primary).

**Phase D — money & admin (§6, §10)**
13. Welcome-bonus crediting on verification (10 TND) + fuel voucher issuance.
14. Wallet screen with top-up + full history; Flouci redirect flow.
15. Admin dashboard consuming the existing `reporting`, `moderation`, `verification`, `brands` APIs.

**Phase E — monetization (§7)**
16. Brand portal, audience targeting by interest tag, impression/redemption counters.

---

## 5. One-line summary

The backend is a competent, well-tested domain core that stops short of the PRD in six specific places (email auth, ride edit/cancel, ride search, interest taxonomy, welcome incentives, verification review API); the frontend is a polished shell that consumes about a quarter of it and is currently running entirely on mock data; and the product's stated differentiator — interest-based matching — does not exist in either layer, because the name `Interest` was taken by a saved-route feature.
