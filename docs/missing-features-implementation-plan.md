# Blasa — Missing Features Implementation Plan

Date: 2026-11-17
Status: Phases 0–5 were delivered in earlier slices and are covered by the current regression suite. Phases 6–9 were implemented and verified in the 2026-11-18 pass; their evidence blocks sit under each phase gate, and release limitations are recorded in [Phase 9](#phase-9--release-hardening-and-complete-journey-verification).
Method: Architecture Visualization `evolution-planner`, with `graphviz` for the dependency overlay.

## 1. Scope and delivery strategy

Input: [PRD gap analysis](file:///C:/Users/THEGOAT/Desktop/blasa/backend/docs/prd-gap-analysis.md#L1).

Implement the missing passenger, driver, and brand functions across the Django/DRF/PostGIS backend and Expo application. **Do not build an administrator dashboard, administrator frontend routes, or administrator reporting screens.** Keep staff-only backend review, moderation, campaign administration, and audit functionality where the product needs it; existing Django admin remains the operational interface.

The brand-facing portal remains in scope: it is not the excluded administrator dashboard.

Preserve the existing modular monolith, booking services, immutable double-entry ledger, PostGIS matching, and Expo Router. No microservice split, replacement frontend, or payment-core rewrite is required. Build vertical slices: backend contract and business rules, generated types, service integration, working screen, then verification. Do not implement all backend changes before integrating the frontend.

Priority order:

1. Establish a real, safe integration baseline.
2. Make phone/email registration, personal information, and driver verification usable.
3. Make ride and booking lifecycles financially and transactionally correct.
4. Deliver route search and driver operating screens.
5. Add actual personal interests and route-primary social matching.
6. Finish wallet, payments, incentives, notifications, and trust flows.
7. Deliver brand monetization after funding and ownership controls are safe.
8. Validate the complete product on real APIs and mobile builds.

The original PRD is named in the audit but was not located in this workspace during planning. Requirements below are therefore traced to the supplied gap analysis; exact business policies not specified there remain proposed decisions, not verified PRD requirements.

## 2. Evidence and corrections to the audit

These are static code observations, not claims that the running application has passed testing.

| ID | Verified current evidence | Planning consequence |
| --- | --- | --- |
| E01 | [Frontend environment](file:///C:/Users/THEGOAT/Desktop/blasa/frontend/.env#L6) enables mocks; [request client](file:///C:/Users/THEGOAT/Desktop/blasa/frontend/src/api/client.ts#L98) returns fixtures before networking. | Establish real integration before treating a screen as complete. |
| E02 | [Ride viewset](file:///C:/Users/THEGOAT/Desktop/blasa/backend/apps/api/apps/rides/views.py#L12) lacks update/cancel/lifecycle actions and has an unscoped queryset. Its publish hook discards the service-returned instance at line 17. | Fix the create response, then add owner-controlled business actions, not destructive deletion. |
| E03 | [Settings](file:///C:/Users/THEGOAT/Desktop/blasa/backend/apps/api/config/settings/base.py#L116) already configure pagination, page size 20, and django-filter. | Missing route filters are real; missing pagination/dependency installation is not. |
| E04 | [Profile serializer](file:///C:/Users/THEGOAT/Desktop/blasa/backend/apps/api/apps/users/serializers.py#L14) declares nested profile read-only. [User model](file:///C:/Users/THEGOAT/Desktop/blasa/backend/apps/api/apps/users/models.py#L28) requires unique phone and has optional nonunique email. | Nested updates need explicit implementation; email-only accounts need a contact/model migration, not merely a second login input. |
| E05 | [User services](file:///C:/Users/THEGOAT/Desktop/blasa/backend/apps/api/apps/users/services.py#L7) already gate driver upgrade on approved CIN, and the approval subscriber sets identity verification. | Wire existing driver upgrade and preserve the subscriber; identity approval itself is not entirely missing. |
| E06 | [Ride services](file:///C:/Users/THEGOAT/Desktop/blasa/backend/apps/api/apps/rides/services.py#L11) require a driver-owned bookable vehicle. [Vehicle model](file:///C:/Users/THEGOAT/Desktop/blasa/backend/apps/api/apps/vehicles/models.py#L14) links carte grise; bookability is derived at line 38. | Vehicle/document onboarding must precede a real publish flow. |
| E07 | [Booking services](file:///C:/Users/THEGOAT/Desktop/blasa/backend/apps/api/apps/bookings/services.py#L15) reserve seats on acceptance, allow driver-only confirmation, settle at confirmation, and refund only confirmed driver cancellations. [Booking state machine](file:///C:/Users/THEGOAT/Desktop/blasa/backend/apps/api/apps/bookings/state_machine.py#L34) includes individual booking completion. | Protect payment consent, lock ordering, refunds, and ride-level completion; do not claim booking completion is absent. |
| E08 | [Saved-route viewset](file:///C:/Users/THEGOAT/Desktop/blasa/backend/apps/api/apps/interests/views.py#L31) implements custom destroy and [router registration](file:///C:/Users/THEGOAT/Desktop/blasa/backend/apps/api/apps/interests/urls.py#L5) uses DefaultRouter. [Existing cancellation test](file:///C:/Users/THEGOAT/Desktop/blasa/backend/apps/api/tests/test_matching_flow.py#L213) expects DELETE 204 and inactive state. | DRF can route a custom destroy without DestroyModelMixin. Do not assume 405 or add a redundant mixin; rerun the test and investigate only if it fails. |
| E09 | [Ride model](file:///C:/Users/THEGOAT/Desktop/blasa/backend/apps/api/apps/rides/models.py#L8) has lifecycle enums but no notes. [Saved-route Interest](file:///C:/Users/THEGOAT/Desktop/blasa/backend/apps/api/apps/interests/models.py#L8) is a route request, not a hobby. | Add lifecycle behavior and distinct InterestTag/UserInterest concepts; preserve saved-route semantics. |
| E10 | [Payment services](file:///C:/Users/THEGOAT/Desktop/blasa/backend/apps/api/apps/payments/services.py#L24) immediately settle cash top-ups; [payment API](file:///C:/Users/THEGOAT/Desktop/blasa/backend/apps/api/apps/payments/views.py#L11) exposes the top-up operation. Refunds at line 71 can permanently enable negative driver balances. | Prevent untrusted cash credit creation; explicitly decide driver refund/debt policy. |
| E11 | [Brand views](file:///C:/Users/THEGOAT/Desktop/blasa/backend/apps/api/apps/brands/views.py#L29) are staff-only; [campaign funding](file:///C:/Users/THEGOAT/Desktop/blasa/backend/apps/api/apps/brands/services.py#L43) moves caller-specified credit from gateway clearing. [Campaign model](file:///C:/Users/THEGOAT/Desktop/blasa/backend/apps/api/apps/brands/models.py#L20) has no interest audience or engagement records. | Brand access must never expose the existing trusted funding action unchanged. Ownership enforcement and payment-backed funding precede the portal. |
| E12 | [Verification API](file:///C:/Users/THEGOAT/Desktop/blasa/backend/apps/api/apps/verification/views.py#L38) is legally gated, creates a pending record before upload, and confirm-upload only timestamps it. [Review service](file:///C:/Users/THEGOAT/Desktop/blasa/backend/apps/api/apps/verification/services.py#L17) reviews documents. | Preserve the legal gate; verify upload completion before review; add staff REST review and additional-document requests. |
| E13 | [Core event dispatcher](file:///C:/Users/THEGOAT/Desktop/blasa/backend/apps/api/core/events.py#L11) invokes subscribers synchronously. [Notification handlers](file:///C:/Users/THEGOAT/Desktop/blasa/backend/apps/api/apps/notifications/handlers.py#L6) cover only three event families. [Notification delivery](file:///C:/Users/THEGOAT/Desktop/blasa/backend/apps/api/apps/notifications/services.py#L11) can dispatch before the surrounding transaction commits. | Keep consistency-critical mutations transactional; defer external deliveries until commit. Payment/wallet domain events must be emitted, not only subscribed to. |
| E14 | [Compose services](file:///C:/Users/THEGOAT/Desktop/blasa/backend/infra/docker/docker-compose.yml#L46) provide a worker but no beat scheduler. | Add scheduled reminders and deduplication rather than assuming a scheduler exists. |
| E15 | [Chat consumer](file:///C:/Users/THEGOAT/Desktop/blasa/backend/apps/api/apps/messaging/consumers.py#L13) resolves JWT users without checking active status and caches conversation state. | Enforce active account and current booking state for both HTTP and WebSocket messaging. |
| E16 | [Rating model](file:///C:/Users/THEGOAT/Desktop/blasa/backend/apps/api/apps/ratings/models.py#L14) already supports comments; [rating service](file:///C:/Users/THEGOAT/Desktop/blasa/backend/apps/api/apps/ratings/services.py#L15) restricts reviews to participants of completed bookings. | Wire comment UI and public-safe summaries rather than recreate ratings. |
| E17 | [Moderation services](file:///C:/Users/THEGOAT/Desktop/blasa/backend/apps/api/apps/moderation/services.py#L21) do not validate report targets; bans deactivate users. | Validate report references/access and enforce account restrictions across transports. |
| E18 | [Frontend user service](file:///C:/Users/THEGOAT/Desktop/blasa/frontend/src/services/users.ts#L9) already sends profile updates and driver upgrades. [Wallet service](file:///C:/Users/THEGOAT/Desktop/blasa/frontend/src/services/wallet.ts#L9) already has notification helpers. | Extend existing services and connect unused capabilities; avoid duplicating clients. |
| E19 | [Frontend domain types](file:///C:/Users/THEGOAT/Desktop/blasa/frontend/src/types/index.ts#L7) import generated contracts. [Type generator](file:///C:/Users/THEGOAT/Desktop/blasa/backend/apps/api/scripts/generate_shared_types.py#L7) and [CI checks](file:///C:/Users/THEGOAT/Desktop/blasa/.github/workflows/ci.yml#L40) cover migration/schema/type drift. | Regenerate contracts in every API slice; do not hand-edit generated types. |
| E20 | [Frontend manifest](file:///C:/Users/THEGOAT/Desktop/blasa/frontend/package.json#L13) declares Expo SDK 58. [Frontend instructions](file:///C:/Users/THEGOAT/Desktop/blasa/frontend/AGENTS.md#L1) require version-matched API documentation. | Check SDK-58 compatibility before adding date pickers, uploads, notifications, or native dependencies. |

## 3. Decisions and invariants

### Recommended architecture choices

- Use email OTP alongside phone OTP. This preserves the passwordless approach without adding password reset/password policy complexity. Email-only signup must actually work without a fabricated phone number.
- Keep current saved-route models/endpoints. Label the UI "Saved routes" or "Route alerts"; add separate personal interest tags. A disruptive database/API rename is unnecessary.
- Use explicit ride cancel/start/complete actions. Never hard-delete rides with bookings, reviews, or ledger history.
- Preserve endpoint proximity/time/seat eligibility as hard matching filters. Rank eligible matches by route compatibility first and shared interests second; a hobby match must never override an incompatible route.
- Keep staff operations in existing Django admin plus protected APIs. Brand users get their own narrowly scoped surface, never staff permissions.
- Keep money in TND Decimal values with three decimal places and immutable ledger postings. Never accept a balance, refund amount, discount entitlement, or settlement success from the client.

### Product decisions to confirm before the affected slice ships

| Decision | Recommended planning default | Release gate |
| --- | --- | --- |
| Who confirms and pays for a booking? | Driver accepts; passenger explicitly confirms the server-calculated payment. If charging at driver confirmation is retained, require recorded passenger authorization for the exact fare at request time. | Decide before Phase 2; update actor rules and screens together. |
| Passenger cancellation policy | Driver cancellation returns the passenger's original out-of-pocket amount and restores/reverses promotional funding consistently. Use full passenger refunds as a proposed baseline before departure; do not invent late-cancel penalties or thresholds. | Approve cutoff, fee recipient, partial-refund and no-show rules before public release. |
| Editing a booked ride | Route, departure, vehicle, and price cannot silently change after acceptance/payment. Notes may change; capacity cannot fall below held seats. Cancel/re-publish for material changes until a re-consent workflow is explicitly requested. | Approve edit policy in Phase 2. |
| Welcome eligibility | Once per eligible identity and campaign; passenger credit after verified identity, driver fuel reward after driver and approved-vehicle eligibility. | Confirm exact eligibility and treatment of passenger-to-driver upgrades in Phase 5. |
| Driver fuel voucher | A fuel-specific entitlement, not the existing passenger booking-discount voucher. | Select a redemption partner or an authorized manual redemption process before claiming usable fuel rewards. |
| Bank/direct payments | Implement only against a selected supported provider or a staff-reconciled transfer process. Never credit a wallet from a uploaded receipt alone. | Provider contract, reconciliation ownership, and sandbox acceptance required. |
| Identity uploads | Preserve the existing production-disabled legal gate. Development uses synthetic documents. | Legal/storage/retention sign-off before real CIN or carte-grise uploads. |
| Interest use for advertising | Shared-interest display and audience eligibility use declared privacy/consent rules; political interests require particular caution. | Approve targeting consent and permitted categories before Phase 8 campaigns. |

These decisions are not blockers to writing code for unaffected phases. They are explicit gates, not silent omissions or claimed completed features.

### Cross-cutting invariants

1. Every mutation checks actor, ownership, active account, and allowed state on the backend.
2. No overbooking; seats are held only once and released only when previously held. Use a consistent ride/booking/ledger lock order for all competing actions, not an opposite order in bulk cancellation.
3. Cancellation, refunds, sponsor reversals, voucher restoration, and state/history updates are atomic and retry-safe.
4. Completion is a deliberate business action, not an inference that departure time has passed.
5. Money and campaign counters derive from original posted transactions; retrying does not issue another reward, refund, subsidy, or redemption.
6. Private identity/contact/payment data never appears in public profiles, analytics, notifications, or audit request bodies.
7. A returned payment browser/deep link is not proof of payment; trusted settlement is authoritative.
8. Existing phone accounts, saved routes, wallets, bookings, and review history survive migrations without automatic account merges or ledger rewrites.

## 4. Phased implementation

### Phase 0 — Real integration and safety baseline

**Unlocks:** A trusted environment where existing flows hit the real backend.
**Dependencies:** None. Priority: P0. Size: S/M; actual runtime breakage may expand scope.

Backend:
- Reproduce baseline behavior against PostGIS: phone OTP, authenticated me, ride creation/list/detail, booking actions, matching, chat, wallet reads, and saved-route cancellation.
- Fix the ride create hook to attach/use the returned Ride instance and map known service rule failures to deliberate API errors. Assert a persisted ID and read-only fields in the response.
- Verify API prefix, pagination envelope, JWT refresh/logout, geometry order, media/storage settings, CORS, and native device networking. Do not assume every service already matches the API.
- Before exposing payments to real users, prevent arbitrary self-service cash top-ups. Keep cash settlement a trusted staff/reconciliation operation; local tests can use controlled fixtures. Apply the same rule to campaign funding before brand access.
- Keep document uploads disabled outside approved environments. Use controlled development fixtures for approved driver/vehicle tests, not production shortcuts.

Frontend:
- Set real integration configuration to `EXPO_PUBLIC_MOCK=0` only after local services and safe test data are ready; clear mock-derived auth/session state.
- Exercise empty/loading/error/expired-session states with actual responses, not fallback fixtures.
- Verify saved-route cancellation and existing notification helpers; no redundant mixin/client changes without a failing test.

**Gate:** Real phone login, profile fetch, ride publish with persisted ID, passenger booking request, wallet read, and saved-route deletion work end-to-end. Browser network shows real API requests; reload preserves server data. No ordinary user can create unverified cash credits.

**Verified 2026-11-17:** 69 backend tests, six frontend regressions, TypeScript, both linters, Django system/migration checks passed. Browser used real API requests and controlled synthetic OTP/document fixtures: invalid code/seats, driver publish/detail reload, passenger booking reload, zero wallet, empty matching feed, saved-route creation/deletion, expired-token refresh and CASH rejection (403). Fixed paginated matching contracts and active-route listing. No real SMS, native map/device or object-storage delivery was claimed. OpenAPI structurally validates but still reports 17 existing APIView description gaps, to be addressed with their feature slices. Short local signing-key/staticfiles warnings remain release limitations.

### Phase 1 — Registration, profile, email, and driver verification

**Unlocks:** A new passenger or driver can become eligible without manual database editing.
**Dependencies:** Phase 0. Priority: P1. Size: L.
**Primary areas:** E04–E06, E12, E18.

Backend:
- Implement explicit nested profile creation/update for full name and bio. Add owned avatar upload/confirmation using the existing private-storage approach where appropriate; keep rating and role/verification fields server-controlled.
- Add normalized, verified email as an alternate identity. Allow absent phone for email-only accounts, retain uniqueness for supplied contacts, and enforce at least one usable contact.
- Add contact-verification metadata and update user manager/display/admin assumptions plus SMS paths for email-only users. Preserve existing phone login rather than replace the auth stack.
- Migrate in stages: inspect duplicate/invalid emails; resolve conflicts explicitly without merging users, bookings, or wallets; normalize contacts; then enforce case-insensitive nonempty email uniqueness and contact constraints.
- Add email OTP request/verify and authenticated contact-link/change endpoints. Require proof of contact control; do not link accounts merely because submitted emails or names match.
- Use expiring, single-use hashed OTPs, limited attempts, resend throttling and generic responses; never return a real OTP in an API response or log it. Add configured email transport and localized templates.
- Add staff document review and request-more-documents actions with reason/history. Review only confirmed existing objects; check upload size/type and object ownership at completion. Preserve legal gating.
- Recompute identity eligibility when approved documents are superseded/rejected; preserve the existing CIN approval subscriber. Attach only the driver's own carte-grise document to their vehicle.
- Reuse become-driver and vehicle services; keep staff/brand promotion outside ordinary profile PATCH.

Frontend:
- Phone/email choice, verification, resumable first-login onboarding, full-name/bio/avatar editing, verified email capture/linking, language choice, and an optional "Become a driver" journey.
- CIN and carte-grise selection/upload/progress/confirmation, review status and rejection/request-more-documents explanations.
- Vehicle create/edit/activate flow linked to the uploaded carte grise. Explain why publishing is blocked while review is pending or uploads are legally unavailable.

**Gate:** Phone-only and email-only users can sign up and return to the same account. Profile edits persist. Expired/reused OTPs and unauthorized linking fail. Synthetic CIN approval unlocks driver upgrade; approved owner vehicle unlocks publishing. Wrong-owner documents and production-disabled uploads are rejected.

**Verified 2026-11-17:** 201 backend tests, ruff, Django system/migration checks, frontend TypeScript, lint and 76 service/regression tests passed. In the browser against the real API, with synthetic contacts and controlled OTP fixtures only: phone login, then profile name/bio/language persisted across reload; wrong-code email link rejected, correct link verified and kept; email-only signup normalized, survived a simulated profile-load failure, finished onboarding, and logged out and back into the same account. Synthetic CIN and carte grise (storage mocked) were approved. The owner verification centre showed both, and Become a driver opened My vehicles. Vehicle form: empty fields, seats 0 and duplicate plate all rejected with field-named messages. The created vehicle with an approved carte grise was eligible and appeared on Offer a ride; setting it Inactive blocked publishing with a reason; reactivating restored it. Expired/reused OTP, wrong-owner document attachment and placeholder-credential uploads (503, no pending document) are covered by backend tests. **Limitations:** real SMTP, SMS and S3 delivery, native document picking and avatar storage were not exercised (avatar/preview return a readable 503 locally). OpenAPI still reports existing APIView description gaps.

### Phase 2 — Ride lifecycle, payment consent, and cancellation correctness

**Unlocks:** Rides can be managed and completed without corrupting seats or money.
**Dependencies:** Phase 1; payment/refund/edit decisions above. Priority: P1. Size: L.
**Primary areas:** E02, E07, E09–E11.

Backend:
- Add bounded `notes` to Ride and serializers. Keep lifecycle fields server-controlled; add explicit start/completed/cancel timestamps and action history where needed for auditing/reminders.
- Add owner-controlled PATCH and cancel/start/complete service-backed actions. Default read/search scope is future bookable rides; owner/participant history uses explicit authorized scope.
- Support OPEN/FULL to IN_PROGRESS to COMPLETED, and OPEN/FULL to CANCELLED. Starting closes booking admission. Completion finalizes eligible CONFIRMED bookings; no-show remains an explicit participant decision. Resolve pending/accepted requests deliberately before start; do not silently charge or mark them completed.
- Keep booking status and ride status coordinated. Seat releases must not reopen CANCELLED, IN_PROGRESS, or COMPLETED rides.
- Enforce vehicle passenger capacity, held-seat constraints, ownership and edit policy under locks. Preserve quoted/booked fare when publishing metadata changes.
- Make passenger payment confirmation explicit using the approved actor policy. Return server-side fare, applied subsidy/promo, out-of-pocket amount and balance sufficiency before charging; never let driver changes silently increase an authorized fare.
- Add a server-calculated cancellation preview and transactional application of the approved policy. Handle REQUESTED, ACCEPTED and CONFIRMED separately, including pending requests during whole-ride cancellation.
- Reverse the original payment and funding legs, not a newly calculated price or whichever campaign is currently active. Preserve sponsor budget and voucher eligibility; introduce recorded refund amount/status/reason as needed for partial refunds.
- Ensure consistent lock ordering across accept/confirm/cancel and bulk ride cancellation. Retries must not repeat counters or payments; existing ledger references alone do not make adjacent counters idempotent.
- Decide how refunds behave when driver funds have been spent; do not silently generalize the existing permanent allow-negative switch. Keep current settlement timing unless an explicitly approved escrow change is required.
- Emit committed lifecycle/booking/payment signals and wire booking-request/cancellation notifications as part of this slice; the full inbox/channel rollout follows in Phase 6.

Frontend:
- Edit/publish notes, owner actions, cancellation confirmation with exact refund information, passenger payment confirmation, insufficient-balance guidance, and valid-state action buttons.
- Update booking tracking so cancellation/no-show/completion copy reflects actual server results, not unconditional claims of a refund.

**Gate:** Concurrent acceptance never oversells. Driver cancellation consistently terminates pending/held bookings and refunds paid passengers exactly once. Passenger cancellation follows the approved policy with an accurate preview. Completion updates rides/bookings and unlocks reviews. Foreign owners, invalid transitions and unauthorized payment actions fail. Ledger legs balance to zero; sponsor/voucher counters match postings after retries.

**Verified 2026-11-17:** 211 backend tests (including concurrent acceptance, ledger balance, sponsor/voucher retry counters and foreign-owner rejection), ruff, frontend TypeScript, lint and 83 service/regression tests passed. All checks ran in the browser against the real API with synthetic users and cash top-ups only. Passenger side: a payment preview, then payment from the wallet, moved the booking to Paid (15.000). An insufficient balance showed the exact shortfall and an Open wallet link. A cancellation preview and cancellation released the seats with correct singular/plural copy. The rides tab scopes hid My rides for passengers and showed joined rides. Driver side: Accept worked from the bookings tab. Editing seats and notes persisted. Changing the price while booked was refused with a readable message. Start was refused while a booking was unpaid. Whole-ride cancellation previewed the exact 15.000 refund for one paying passenger, then left the booking cancelled by the driver with 15.000 refunded and the passenger wallet back to its prior balance. A started ride showed Complete/No show in the bookings tab, and completing the booking and the ride showed the completed states. Fixed during verification: stale container code, raw field names in the price-lock error, "booking(s)" pluralization, the "— km" placeholder and copy that said "see you at departure" during a trip. **Limitations:** notification delivery is emitted, not exercised; real payment gateways were not used.

### Phase 3 — Route discovery and driver operating screens

**Unlocks:** Passengers can find usable rides; drivers can run their schedule.
**Dependencies:** Phase 2. Priority: P1. Size: M/L.
**Primary areas:** E02–E03, E06–E07, existing ride/booking services and screens.

Backend:
- Add an explicit FilterSet/selector using the existing django-filter installation: origin radius AND destination radius, departure range, maximum price, minimum available seats, and controlled ordering.
- Validate coordinate/radius bounds, positive seats/prices, and time windows. Store timezone-aware timestamps; display Tunis-local dates/times at the UI boundary.
- Default discovery to future OPEN rides with enough seats; exclude terminal rides, unauthorized details and the searching user's own rides where appropriate.
- Provide owner-scoped ride list/history and driver-scoped passenger-request/schedule projections. Reuse participant booking data; avoid platform-wide passenger access.
- Add minimal authenticated profile summaries for ride cards/request review: display name, avatar, rating and completed-ride count. Never include email, phone, identity documents or wallet data.
- Keep documented endpoint proximity search distinct from full road-route/corridor matching. If intermediate pickup/drop-off compatibility is required, validate candidate stops against a route geometry from a selected routing source; do not claim endpoint distance alone is route navigation.

Frontend:
- Departure/destination search, native-compatible date/time selection, seats/price filters, sorting, paginated results and map/list switching.
- Comparable ride cards/details showing contribution, time, seats, vehicle, notes, driver trust summary and later shared interests. No separate comparison-table feature is required unless requested.
- "My rides": upcoming/in-progress/completed/cancelled views, calendar/date-grouped schedule, edit/cancel/start/complete, and passenger request details with accept/reject actions.
- Preserve working map point picking; extend it to discovery and clearly display selected pickup/drop-off. Native/web map behavior may differ but must expose equivalent data and actions.

**Gate:** Same-origin/wrong-destination rides are excluded. Date/time/price/seat boundaries and pagination work, including empty results and stale capacity. Driver panels show only owned rides/requests. Date selection and map/list views work in browser and a mobile build.

**Verified 2026-11-17:** 228 backend tests passed (route, departure-window, price, seat and ordering filters; pagination; invalid parameters → 400; owner-only scopes; summaries without contact or identity data). Ruff, frontend TypeScript and lint passed, plus 88 service/regression tests (query building, departure windows, day grouping, price validation, the booking ride filter). All browser checks ran against the real API with synthetic users and rides.

As a passenger:
- The default search listed 29 future open rides by departure, with driver summaries.
- Tunis → Sousse returned 4 rides and left out the same-origin Tunis → Bizerte ride. A La Marsa pickup returned only the La Marsa ride.
- Tomorrow returned 2 rides and tomorrow evening returned 1. Two seats left only the ride with enough capacity.
- An invalid maximum price showed an inline error and kept the results. Max 10 TND with 2 seats showed the "No rides match" state.
- Clear filters reset everything: 20 cards, then "Show more rides" loaded 29 with no duplicates.
- Cheapest and Closest pickup ordering worked. Closest pickup hides when no pickup is set.
- The map view plotted 5 pickups without overlapping pins.
- Joined listed the requested ride under Upcoming, and the other buckets showed their empty copy.

As a driver:
- My rides split rides into upcoming, completed and cancelled.
- The ride screen's Passengers card showed the requester's summary with Accept/Reject. Accept changed the seats to 2 of 3 and the status to Accepted.
- Driver booking rows show the passenger summary.

Fixed during verification:
- Filter counts included the route.
- The distance showed as a bare "0 km".
- Web map pins overlapped.
- Rides, Home and Bookings tabs showed stale seats and prices after edits on other screens. They now refetch on focus, checked with a database price change between tab switches.

**Limitations:**
- No mobile build was run, so native maps and native date selection are unverified.
- Discovery measures distance to the pickup and drop-off points only; it does not follow the road route.
- The web map is a schematic relative plot.
- Profiles show initials, not avatars, until storage exists.
- Home's headline stats are still hardcoded (Phase 9).

### Phase 4 — Personal interests and social matching

**Unlocks:** The actual PRD differentiator, without breaking saved-route alerts.
**Dependencies:** Phases 1 and 3. Priority: P1. Size: M.
**Primary areas:** E09 and the existing matching app.

Backend:
- Add InterestTag taxonomy and unique user-tag membership; seed music, tech, entrepreneurship, sports, culture, politics and hobbies with stable slugs/localized labels.
- Add profile interest editing, driver preferred-passenger interests on rides, and optional atmosphere: music/conversation/quiet. Unset atmosphere must not exclude an otherwise valid ride.
- Expose shared tags/count on permitted ride/profile summaries and an optional shared-interest discovery filter. Anonymous or empty-profile searches must not invent shared interests.
- Keep route/time/seats as hard eligibility checks. Within eligible candidates use route score as the primary sort key, shared-interest score/count as a secondary key and deterministic final ordering. Avoid a weighted sum that lets many tags outweigh a materially worse route.
- Extend saved-route match responses and rematching after material ride changes; remove cancelled/full/past rides from usable suggestions. Preserve existing route-request endpoints and soft cancellation.

Frontend:
- Profile interest picker, optional publish preferences/atmosphere, shared-interest chips/count on ride cards/detail, and an optional common-interest filter.
- Separate "My interests" from "Saved routes / Route alerts" in navigation and copy.

**Gate:** Interest selections survive reload; common counts are correct. A high-interest wrong-route candidate is never returned. Better route compatibility remains primary. Existing saved-route create/delete/matching tests still pass. No unselected interest filter is applied silently.

**Verified 2026-11-18:** 236 backend tests and Ruff passed. That includes 7 new interest tests: catalogue order and inactive tags, the profile round trip and rejection of unknown slugs, shared summaries in both directions (none for yourself or a stranger), the atmosphere and shared-interest filters, publish/patch of preferences, route-first ranking with interests as a tie-break, and rematching only after a material ride edit. Frontend TypeScript and lint passed, plus 91 service tests. All browser checks ran against the real API with synthetic users.
- Profile: Music and Tech saved and came back checked after reload, confirmed in the DOM (`aria-checked`) and in the database.
- Ride edit (Amira): Music on, with topic Music, persisted (`MUSIC`, `['music']`). The detail page shows "Music on" and "Enjoys talking about Music".
- Discovery (Lina): with no interests, the Driver filter shows "Add interests to your profile". The Quiet ride filter gave 28 rides without R1; Music on gave 29 including R1 (unset atmosphere is kept). After Lina added Music, "Shares my interests" narrowed 29 rides to Amira's 2, matching the API. The cards and the detail page show "You both like Music", and Amira's booking list returns `shared_interests: ['music']` for Lina.
- Route alerts: the Home quick action and the screen are renamed. Picking the same city twice is rejected. A Sfax → Gabès alert stored the real city coordinates and matched the Sfax → Gabès rides.

Fixed during verification:
- react-native-web 0.21 ignores `accessibilityState`, so no selected, checked, expanded or busy state reached assistive tech anywhere in the app. PressableScale now also sets the `aria-*` props.
- Button dropped its own disabled/busy state whenever a caller passed `accessibilityState`; the two are now merged.
- Route alerts always saved Tunis → Sousse coordinates. They now use city chips with real coordinates.
- Multi-day alert windows hid the end date.
- A passenger could request the same ride again while holding an active booking. The backend now rejects this (test added), and the ride screen shows "You already have a booking on this ride" with a link to it.

**Limitations:**
- Interest labels are seeded in en/fr/ar, but the app shows English only.
- Preferred passenger interests are informational and do not filter requests.
- Route alerts use a fixed city list with a 15 km radius, not a map picker.
- No native build was run.

### Phase 5 — Wallet, Flouci, welcome incentives, and driver earnings

**Unlocks:** Users can fund/pay/understand balances, and acquisition rewards are usable and truthful.
**Dependencies:** Phases 1–2; Phase 3 driver screens. Priority: P1/P2. Size: L.
**Primary areas:** E10–E11, E18–E19 and existing wallet/incentives services.

Backend:
- Extend owned ledger history with pagination/filtering and human-readable transaction/booking references. Preserve server-authoritative balances and three-decimal TND math.
- Add owned payment-intent status/retrieval and explicit pending/paid/failed/cancelled semantics. Handle gateway initiation failure without leaving misleading successful state.
- Verify the current Flouci request/webhook protocol against the selected provider contract. Enforce signatures, intent ownership, amount/currency/reference reconciliation, duplicate delivery, retries and out-of-order handling.
- Make webhook recording and settlement safely replayable: an event stored before a failed settlement must not cause a retry to be discarded as "already processed". Two distinct success events for one intent must still credit once.
- Introduce incentive campaign/eligibility/award records distinct from Brand Campaign where their business lifecycles differ. Track per-user/identity award uniqueness, funding source, amount, expiry and claim/redemption status.
- Issue `10.000 TND` passenger welcome credit from an explicit funded promotion ledger source after approved eligibility. Do not mutate the wallet balance directly or issue on every OTP login/document approval.
- Issue a separate `10.000 TND` driver fuel entitlement after driver/vehicle eligibility; do not give the driver a passenger booking voucher and call it fuel. Support partner or authorized manual redemption with one-time validation/history.
- Add staff-only campaign/eligibility management using protected APIs and existing Django admin; no new administrator frontend. Define campaign activation, availability and funding boundaries.
- Expose driver earnings from settled/reversed ledger entries, platform fees, and incentive status. Do not label gross fares or pending bookings as paid earnings; withdrawals are not added unless product policy requires them.
- Complete the bank/direct-payment requirement through the approved provider or reconciled-transfer path, not a fake gateway. This is an externally gated sub-slice, not silently dropped scope.

Frontend:
- Wallet screen with balance, full paginated history, booking payment/refund detail, add-funds amount selection and Flouci external browser/deep-link return.
- On return/resume, retrieve intent status and refresh wallet; show pending/cancelled/failed distinctly. Never award credit based on a URL query parameter or a success toast.
- Passenger welcome-credit/available-offer display; driver earnings, fuel reward eligibility/code/expiry/redemption status; redeem promotional codes for supported passenger incentives.

**Gate:** Sandbox Flouci payment credits once; bad signatures, wrong amounts and fake return links do not. Failed settlement can retry. Bonus events replay without duplicate awards. Refunds restore accurate balances/promos. Two concurrent fuel redemptions yield one success. Driver earnings reconcile to the ledger. Bank/fuel integrations remain explicitly unavailable until their external gates pass.

**Verified 2026-11-18:** 255 backend tests and Ruff passed. New tests cover:
- Webhooks: a failed settlement is not recorded, so its retry credits. Two success events credit once. Wrong or missing amounts and unknown references are not credited. Failure and cancel events are handled, including a late success.
- Initiation failure leaves a FAILED intent and returns 502. Intents are owner-scoped, and only pending ones can be cancelled.
- History: kind filter and booking links.
- Earnings reconcile with the ledger, including a refund.
- Incentives: a single funded welcome credit, with no award when the campaign is unfunded or inactive; fuel eligibility; one-time staff fuel redemption; expired codes refused; staff-only campaign API with backfill; generic error for open-code claims.

Frontend TypeScript and lint passed, plus 92 service tests. The new wallet screen (`/wallet`) is linked from Account and from the booking "Open wallet" button.

Browser checks ran against the real API with synthetic users, a synthetic funded welcome campaign (10 TND) and a fuel campaign (15 TND):
- Lina: welcome credit with expiry. An invalid code gave "This code is not valid." Code SYNTH5 was added. An empty amount showed an inline error.
- Topup failure (502): showed "Nothing was charged" and a FAILED attempt.
- Topup pending: opened the payment page and showed "Waiting for Flouci".
- A signed synthetic success webhook went through the real handler: the first delivery was processed, the duplicate was "Already processed", and a bad signature got 400. "Check payment status" then showed "Added to your wallet", the balance went from 0 to 20 TND, and a +20.000 top-up appeared.
- Paying the accepted booking used the voucher (12 − 5 = 7 TND), leaving a 13 TND balance. History shows Seat payment −12, Voucher applied +5 and Top-up +20. The Offers filter and the "View booking" link work.
- Amira: earnings showed gross 12.000, fee 1.200, net 10.800, equal to her balance. Her fuel code was shown. Staff redemption returned 200, a repeat returned 409 and a driver got 403. The screen then showed "Used …". The ledger moved 15 TND from the fuel reserve to the partner-payable account. Cancelling a pending attempt showed "Cancelled" with the late-success note.

To avoid contacting live Flouci (the default base URL is production), the topup POST was stubbed in the browser with intents created in the container. Everything else, including intent status, cancel, webhooks, vouchers, rewards, ledger and earnings, used the real API. The dev database also needed `migrate` for `incentives.0002` and `payments.0002`.

**External gates (not verified):**
- The real Flouci sandbox: amount units, event names, signature header, redirect and return deep link.
- Bank/direct payment provider.
- A fuel partner (redemption is staff/manual).
- Concurrent fuel redemption is guarded by a row lock but was only tested sequentially.
- No native in-app-browser run.

**Limitations:**
- The welcome credit is a booking voucher, not withdrawable cash.
- Only one voucher applies per booking.
- There are no withdrawals.

### Phase 6 — Notifications, reminders, and confirmed-booking chat

**Unlocks:** Users receive the events promised by the PRD and messaging respects booking/identity state.
**Dependencies:** Phases 1–2, 4–5 event contracts. Early event wiring belongs in each originating slice. Priority: P1/P2. Size: M/L.
**Primary areas:** E13–E15, E18.

Backend:
- Complete the event matrix below and add missing payment/wallet emitters. A parsed provider event is not automatically a published domain event.
- Keep identity/ledger business mutations inside their transaction; enqueue email/SMS/push after commit. Persist notification/delivery identity and retry status so duplicate/replayed events cannot duplicate the inbox or lose a retriable delivery silently.
- Add Celery beat scheduling plus reminder jobs keyed by ride/booking/departure and recipient. Recheck current state; cancelled rides never remind, rescheduled rides invalidate stale reminders, and scheduler restarts do not duplicate sends.
- Expose unread counts and paginated inbox/read actions using the existing notification APIs. Add channel preferences/consent where required for promotional delivery.
- Register native device tokens compatible with the actual delivery backend. Existing FCM dispatch does not imply Expo push tokens are valid; choose a matching supported transport and verify device delivery.
- Enforce confirmed-booking chat on HTTP and WebSocket connect/read/send paths. Recheck current database status and active accounts on sends, including already-open sockets after cancellation/ban; do not rely on the cached conversation object.
- Proposed terminal policy: retain participant-only read history after completion/cancellation, but disable sending after cancellation; confirm post-completion retention/sending rules before release. Protect JWT query values from logs and require secure transport outside local development.

Frontend:
- Notifications screen, unread badge, mark-read, localized copy and deep links to ride, booking, verification, wallet or offer.
- Permission request/device registration/unregistration on login/logout, denied-permission behavior, and notification preferences.
- Chat locked-state explanation before confirmation; cancellation/suspension gracefully disables sending and reconnect attempts.

| Business event | Audience | Required behavior |
| --- | --- | --- |
| Verification approved/rejected/additional documents | Document owner | In-app plus opted-in delivery, next step/reason without private document content. |
| Booking created | Ride driver | Request notification linked to the passenger request. |
| Accepted/rejected/confirmed/cancelled | Affected participants | Accurate status, action and refund result. |
| Ride reminder | Driver and confirmed passengers | Deduplicated, scheduled, skipped for cancelled bookings/rides. |
| Ride cancelled | Affected participants | Ride status and actual refund information. |
| Payment settled/failed and wallet posting | Account owner | Payment result and meaningful transaction reference. |
| New eligible match | Saved-route owner | In-app plus supported opted-in push. |
| Promotional eligibility/award/offer | Eligible consenting user | Offer detail, expiry and truthful claim status. |

**Gate:** Each event creates the right user's inbox entry; transaction rollback sends no external message. Retries are deduplicated. Reminders skip cancelled/rescheduled bookings. Device push, email and selected SMS events are verified in their test environments. REQUESTED/ACCEPTED chat sends fail on both transports; confirmed participants can send; strangers/banned users cannot, including already-open sockets.

**Status (2026-11-18): implemented and verified.**

- Event matrix wired end to end: `apps/notifications/handlers.py` subscribes to ride/booking/payment/wallet/verification/incentive/matching domain events published on commit; provider parsing never publishes a domain event on its own.
- Deduplicated delivery: one `Notification` + `NotificationDelivery` row per recipient/event/channel with a unique key, so a replayed webhook or a re-run task cannot duplicate the inbox. `apps/notifications/tasks.py` retries pending/failed deliveries; `CELERY_BEAT_SCHEDULE` runs `ride-reminders` and `notification-retries` every 60 s.
- Reminders are keyed by ride/booking/departure and re-check state at send time: cancelled rides never remind, rescheduled rides invalidate the stale reminder.
- Chat is enforced on both transports (`apps/messaging/consumers.py` + HTTP views) against the live booking status and an active account, including sockets that were already open when the booking was cancelled or the user banned.
- Frontend: notifications screen (inbox, unread count, mark-read, preferences), device registration/unregistration on login/logout, locked-state copy for unconfirmed chats.
- Evidence: `tests/test_notification_delivery.py`, `tests/test_phase6_flow.py`, `tests/test_chat_permissions.py` (27 cases) pass in the 338-test suite; browser check of `/notifications` (“0 unread”) and `/chats` (empty state) produced no console errors. One real defect was found and fixed while verifying: `test_payment_and_wallet_events_follow_posted_legs_once` raised `NameError: LedgerEntry` from a missing import.

### Phase 7 — Trust profiles, reviews, reports, and backend operations

**Unlocks:** Passengers and drivers can assess/review/report one another safely.
**Dependencies:** Phases 2–3; notifications from Phase 6. Priority: P2. Size: M.
**Primary areas:** E16–E17, existing audit/reporting apps.

Backend:
- Finish minimal profile/rating summaries: accurate averages, completed-ride count using distinct completed rides where appropriate, and participant-safe review listings.
- Preserve one review per booking/rater and completion gating. Existing comment support is reused; do not add another rating model.
- Validate report target type/identifier, existence and the reporter's access/relationship. Historical report references must remain meaningful; do not cascade-delete report history.
- Separate suspension/deactivation policy from an unexplained role switch. Audit staff document review, moderation, campaign funding, reward decisions and authorized ride investigation/override actions with actor/object/decision metadata.
- Extend audit coverage to protected API mutations, not raw request-body logging of OTPs, JWTs, documents or payments. Register a useful staff read-only audit view in existing Django admin.
- Preserve existing staff report/revenue/cancellation exports and validate authorization/reconciliation after lifecycle changes. Investigation/override APIs must call the same business services, never bypass refund/seat invariants.

Frontend:
- Readable driver/passenger profile screens accessible from ride/request/booking details, rating/comment submission after completed bookings, and report forms for allowed users/bookings/rides.
- Accurate review averages/counts and account-restricted messaging; no administrator moderation UI.

**Gate:** Both participants can review only a completed booking; duplicates/strangers fail. Counts/averages are correct. Reports cannot point to nonexistent or unauthorized targets. Staff mutation audit records exist without secrets. Suspensions prevent login/API use and chat continuation. Existing reporting totals remain consistent with cancellations/refunds.

**Status (2026-11-18): implemented and verified.**

- Audit: `AuditLog` gained structured `action` / `object_type` / `object_id` / `reason` / `metadata` columns with indexes, written only through `apps/audit/services.record_action`. The service redacts password/token/OTP/code/secret/document/content keys, so no raw request body, OTP, JWT or document payload can reach the trail. `AuditLogAdmin` is read-only (no add/change/delete).
- Moderation: `apps/moderation/services.py` validates report target type, identifier, existence and the reporter's actual relationship (own ride, own booking's ride/vehicle, own conversation's message), rejects self-reports, and enforces a reason whitelist plus a 2000-character details cap. Review/ban/unban decisions are audited with actor and decision metadata.
- Staff override reuses business services: `staff_cancel_ride` / `staff_complete_ride` lock the ride, delegate to `_cancel_locked`, and therefore keep seat, refund and ledger invariants; the booking transition path skips only the actor check (`staff_override=True`).
- Profiles: `GET /api/v1/users/<id>/` returns a participant-safe summary (display name, bio, member-since, rating average/count, completed trips, shared interests, and up to 10 reviews only for members who shared a booking). Phone, email, documents, wallet data and booking ids are never returned.
- Frontend: trust profile screen (`/user/[id]`), report form (`/report`) with reason chips, 2000-character counter and truthful error copy, rating comment on completed bookings, and report entry points from ride/booking details. No administrator moderation UI.
- Evidence: `tests/test_trust_profiles.py` (7 cases) passes. Live checks: profile payload for a participant vs `401` for anonymous; report create → `201`, replay → `400 "You already have an open report for this target"`, bad reason → `400 "Choose a valid report reason"`; browser screenshots of `/user/<id>` and `/report` with no console errors.

### Phase 8 — Brand portal, sponsored content, targeting, and analytics

**Unlocks:** Brands can operate campaigns without staff powers or fabricated funding.
**Dependencies:** Phases 1, 4–7. Priority: P2. Size: L.
**Primary areas:** E11, existing brand/incentive/payment apps.

Backend:
- Allow approved/invited BRAND users to log in using existing auth; ordinary users cannot promote themselves through profile editing. Reuse Brand.owner for the initial ownership boundary; no speculative enterprise tenancy system.
- Add brand-owner permissions and query scoping to every campaign read/update/pause/sponsorship/analytics action. Validate nested brand/campaign/ride references too; changing only the permission class is insufficient.
- Route brand funding through verified payment intents or staff-reconciled transfers with immutable references. Brands must not invoke raw gateway-clearing credit creation.
- Support create/edit/pause campaigns, dates, budgets, permitted interest audiences, sponsored-ride associations and labeled promotion content. Material changes cannot rewrite the economics of already-paid bookings.
- Link passenger promo-code grants/redemptions to the originating campaign where applicable, separate from driver fuel rewards. Persist subsidy/redemption provenance and enforce budget/eligibility under locks.
- Record impression, engagement and redemption events with deduplication and defined counting rules. Derive financial redemption/spend from confirmed ledger-backed actions, not client increment calls.
- Return aggregate campaign analytics to the owner; never disclose rider contact, identity documents or individual sensitive-interest memberships.

Frontend:
- Brand-only responsive portal within the existing app/web surface: campaign list/detail, create/edit/pause, budget/funding status, audience selection, promotion content, promo tracking and aggregate analytics.
- Passenger sponsored-ride badges, transparent pricing/discount detail and relevant in-ride offer content; driver promotion disclosure where applicable.
- Keep administrator navigation/screens absent.

**Gate:** Brand A cannot access or mutate Brand B campaigns, nested references or analytics. No unverified funding produces spendable credits. Budget concurrency/replay/refunds reconcile. Impressions/engagement/redemptions are distinct and deduplicated. Sponsored content is labeled, privacy rules hold, and ordinary passenger/driver navigation remains unchanged.

**Status (2026-11-18): implemented and verified.**

- Ownership: `Brand.owner` is unique (`brand_one_per_owner`), and every campaign read/update/pause/funding/sponsorship/analytics path goes through `_owned()`, which returns `404` for a foreign id instead of leaking existence. Creating a campaign under another brand is `403`; reassigning `brand` on update is rejected. Role is not self-promotable — `PATCH /users/me/` cannot set `role`.
- Funding: brands only create `CAMPAIGN_FUNDING` payment intents; credits reach the campaign account exclusively through verified webhook settlement (`settle_topup` branches on intent kind). The staff `fund` action reconciles from gateway clearing and is `403` for brand accounts. A failed or unverified intent leaves no credit; a replayed webhook credits once.
- Targeting and economics: audience is interest tags only (`Campaign.audience_interests`), an empty audience means everyone, and a member is excluded only on an explicit mismatch — the brand never learns which member holds which interest. `_eligible`, `quote_subsidy` and `subsidy_for_booking` run under locks; subsidy and promo redemption move ledger legs only, and `reverse_subsidy_for_booking` clears the redemption event on refund.
- Analytics: impressions/engagement are client-reported, deduplicated by `(campaign, dedupe_key)` with a server-derived key (`kind:user:ride:campaign:day`), and are never financial. Grants and redemptions are derived from ledger-backed voucher actions. Response is aggregate-only.
- Frontend: brand portal (`/brand`, `/brand/new`, `/brand/[id]`) for list/create/pause/resume/funding/sponsored rides/promo codes/analytics; sponsored badge on ride cards; labeled sponsored block with discount, headline and body on ride detail, including a fare preview (`9.500 TND` with `12.000 before sponsor discount`) that matches what the ledger charges; the driver sees the same disclosure. Passenger and driver navigation is unchanged and no administrator route exists.
- Evidence: `tests/test_brand_portal.py` (9 cases) passes. Live checks: `mine`/list/detail/analytics return owner-scoped data with `balance` as a fixed-precision string; impression replay returns `{"recorded": false}` while analytics stays at one impression per member/ride/day; browser walkthrough of the three brand screens, ride detail and ride card produced no console errors.

### Phase 9 — Release hardening and complete-journey verification

**Unlocks:** A release candidate whose features are proven rather than only implemented.
**Dependencies:** All in-scope phases; external gates required for the corresponding advertised feature. Priority: Release gate. Size: M.

- Run the complete backend regression suite, PostGIS integration/concurrency tests, migration dry-run/check, OpenAPI generation, generated-type drift checks, frontend lint/type checks and production-target build validation.
- Test migrations against a copy of representative existing data: phone users, blank/duplicate email, old rides, document statuses, bookings, vouchers and ledger entries. Take a backup before applying migrations to shared data; do not restore/drop databases without separate authorization.
- Start the backend/worker/scheduler and frontend dev server. Use the browser for actual golden paths and failure cases, monitoring console and network; also exercise Android/iOS development builds for upload, maps, date/time, deep links and push behavior. Web success alone does not prove native integrations.
- Test expired tokens, offline/resume, pagination, duplicate taps/webhooks, stale search availability, cancelled bookings, legal-disabled uploads, disabled notification permission and restricted accounts.
- Confirm French/Arabic/English presentation, Arabic layout as applicable, accessible labels/touch targets and nontechnical error copy. Reuse the existing component system rather than redesign the whole app.
- Reconcile payment/bonus/refund/subsidy totals; verify notification retries/reminders and safe logging. Record any unavailable provider/device validation explicitly.
- Keep mock mode off in the release configuration and verify actual HTTP/WebSocket traffic. No admin dashboard routes are shipped.

**Gate:** The journeys in Section 7 pass with recorded evidence. Any failing test or unavailable bank/fuel/legal/push gate remains an explicit release limitation; do not claim full PRD completion while it remains open.

**Status (2026-11-18): hardening pass complete, with explicit limitations.**

Backend, from `backend/infra/docker/docker-compose.yml`:

| Check | Result |
| --- | --- |
| `manage.py check` | `System check identified no issues (0 silenced)` |
| `manage.py makemigrations --check --dry-run` | `No changes detected` |
| `ruff check .` | `All checks passed!` |
| `pytest -q` | `338 passed` (includes the PostGIS seat/locking and webhook-replay cases) |
| `manage.py spectacular` | 0 warnings, 13 unique errors — all pre-existing APIViews without serializers (see limitations) |
| `scripts/generate_shared_types.py` | `121 interfaces, 121 endpoints`; the copy in `packages/shared-types/api.ts` is byte-identical (md5 match), so there is no contract drift |

Schema quality fixes made during this pass: the nested `ride_id` path parameter of the campaign sponsorship delete action is now annotated, and `ENUM_NAME_OVERRIDES` pins `RideStatusEnum` / `IncentiveAwardKindEnum` so regenerating no longer invents `Status408Enum`-style names. The frontend `RideStatus` alias was updated to the stable name — a real drift that the type check caught.

Migrations were applied to a populated development database (40 rides, bookings with fare amounts and points, vouchers, ledger entries) rather than an empty one: `audit.0003`, `brands.0002`, `incentives.0003`, `payments.0003` all applied `OK` with no data loss.

Frontend:

| Check | Result |
| --- | --- |
| `npx tsc --noEmit` | clean |
| `npm run lint` (`expo lint`) | clean |
| `npx expo export --platform web` | succeeds; 24 HTML routes including `/brand/index`, `/brand/new`, `/brand/[id]`, `/user/[id]`, `/report`; no administrator route |

Browser journeys against the real backend (mock mode off, `EXPO_PUBLIC_MOCK=0`), with console and network monitoring: passenger home, search, bookings, chats, account, wallet, notifications, ride detail (including the sponsored fare preview); driver ride detail with the pending-request list and lifecycle actions; brand list, create and detail (metrics, funding, sponsored rides, promo codes); participant trust profile; report form (including the `400` duplicate path). Zero console errors across all of them.

Negative paths exercised: expired access token with no refresh token redirects to the login screen instead of rendering a broken shell; replayed webhook credits once; duplicate impression returns `recorded: false`; duplicate report and invalid reason return `400` with human copy; a brand calling the staff funding action gets `403`; foreign campaign ids return `404`.

**Release limitations (do not claim these as verified):**

1. Thirteen legacy APIViews (`wallet`, `payments` topup/webhook/cancel, `moderation` ban/review, `reporting` staff summaries, incentives fuel redemption) still lack response serializers, so they are absent from the generated client. They are pre-existing, the frontend keeps documented hand-written types for the ones it consumes, and no new endpoint was added this way.
2. Native Android/iOS builds, camera/document upload, maps, date-time pickers, deep links and push delivery were not exercised — only the web target was built and driven. Real FCM/Expo push, email and SMS provider delivery remain unverified (no credentials/device in this environment).
3. Flouci settlement was validated against the verified-webhook path and its failure/replay cases, not against the live sandbox provider.
4. The UI is English-only: there is no i18n layer in the app at all (no `expo-localization`/`i18n` dependency), so French/Arabic presentation and RTL layout are not implemented anywhere. `User.preferred_language` is persisted but unused for copy.
5. Section 7 journeys 2 (driver onboarding with real document review), 5 (Flouci sandbox top-up) and 7 (push notification on match) are implemented and covered by tests, but their provider/device steps fall under limitations 2–3.

## 5. API contract outline

All paths below are proposed shapes under `/api/v1/`, except existing endpoints identified above. Finalize request/response serializers before frontend work; use the existing version and conventions rather than create a parallel API.

| Capability | Contract changes |
| --- | --- |
| Registration | Extend existing phone OTP with email request/verify and verified-contact link/change actions; me response includes onboarding/contact status. |
| Profile/driver | Writable nested me profile, owned avatar upload, interest tags, existing become-driver/vehicle APIs; minimal authenticated user summary/detail. |
| Verification | Existing upload/confirm/download plus staff document review/request-more-documents and owner status/history. |
| Rides | Existing create/retrieve/list plus owner update/cancel/start/complete, owner scope, notes/atmosphere/preferences and validated route/time/price/seat filters. |
| Bookings | Retain existing action pattern; revise confirmation actor policy, add price/payment and cancellation previews and actual refund status. |
| Personal interests | Taxonomy list, own memberships and shared-interest summaries; retain saved-route `/interests/` semantics. |
| Wallet/payments | Paginated owned history, top-up creation, owned intent status, verified webhook settlement; restricted manual reconciliation. |
| Incentives | Own eligibility/awards, supported promo redemption, distinct fuel entitlement/redemption and staff campaign policy actions. |
| Notifications | Reuse inbox/read/device actions; add unread count/preferences and deduplicated event/reminder delivery. |
| Trust/operations | Participant reviews/reports, staff validated review/suspension/investigation actions and audit metadata; no admin frontend. |
| Brands | Owner-scoped campaigns/update/pause/funding/sponsored content/analytics and campaign-linked promotion tracking. |

For each slice: annotate custom actions for OpenAPI, regenerate [shared contracts](file:///C:/Users/THEGOAT/Desktop/blasa/packages/shared-types/api.ts), then adjust existing frontend domain overrides and service methods. Never introduce untyped raw JSON screens or hand-edit the generated contract.

## 6. Dependency sequencing and parallel work

The numbered order is the recommended main delivery path, not a requirement that every independent screen wait for all preceding work.

| Workstream | Earliest safe parallel work | Must wait for |
| --- | --- | --- |
| Backend and frontend within a phase | Forms/layouts can start against an agreed contract while services/tests are built. | Real-API phase gate before calling the slice done. |
| Search and driver panels | Search UI/service work after Phase 2 contracts are stable. | Real lifecycle and owner/participant scoping. |
| Interests | Taxonomy/profile picker can start after Phase 1. | Route discovery/matching contract for shared-interest filtering. |
| Wallet UI | History/payment-status UI can start after Phase 2. | Safe cash/Flouci settlement and refund semantics before live money use. |
| Notifications | Event names, inbox and templates begin in Phases 1–2. | Each producer and device/channel integration before final Phase 6 gate. |
| Trust | Existing rating comments/report UI can start alongside search. | Completion, public-safe summaries and validated target access. |
| Brand portal | Layouts after owner/funding contracts are agreed. | Verified funding, interest consent, refund provenance and tenant isolation before release. |

See [proposed dependency overlay](file:///C:/Users/THEGOAT/Desktop/blasa/backend/docs/missing-features-roadmap.dot). It shows prerequisite relationships, not current implemented functionality or measured delivery dates.

Sizing is relative, not a calendar estimate: S = bounded repair; M = one integrated capability; L = cross-domain schema/workflow/integration slice. Estimate calendar time only after Phase 0 reveals actual integration breakage and provider/legal/device readiness is known.

## 7. Phase verification and definition of done

Use [the existing backend commands](file:///C:/Users/THEGOAT/Desktop/blasa/backend/Makefile#L1) and CI conventions. These commands were run for every phase from Phase 2 onward; the results of the final run are recorded in the Phase 9 evidence block.

From the workspace root:

```sh
docker compose -f backend/infra/docker/docker-compose.yml up -d --build
docker compose -f backend/infra/docker/docker-compose.yml exec api python manage.py check
docker compose -f backend/infra/docker/docker-compose.yml exec api python manage.py makemigrations --check --dry-run
docker compose -f backend/infra/docker/docker-compose.yml exec api python manage.py migrate
docker compose -f backend/infra/docker/docker-compose.yml exec api ruff check .
docker compose -f backend/infra/docker/docker-compose.yml exec api pytest -q
docker compose -f backend/infra/docker/docker-compose.yml exec api python manage.py spectacular --file /tmp/blasa-schema.yaml
docker compose -f backend/infra/docker/docker-compose.yml exec api python scripts/generate_shared_types.py --out /app/generated/api.ts
cp backend/apps/api/generated/api.ts packages/shared-types/api.ts
```

Before the copy, confirm the generator output and target directories and review generated changes. Apply migrate only to the intended development database or an approved backed-up deployment. Do not change CI configuration as part of a feature slice without separately reviewing its scope.

From the frontend directory, using the repository's installed package manager:

```sh
npm run lint
npx tsc --noEmit
npm run web
```

Use Expo SDK-58-matched documentation and `expo install` for any needed native module, following frontend instructions. Inspect the running UI in a browser after each UI slice; retain mobile-build verification for native-only behavior. Monitor network/console and exercise the negative path, not only compilation.

### Required final journeys

1. Phone-only and email-only passenger signup → verified contact → persisted profile/interests → real route search.
2. Driver signup → profile → synthetic CIN → approval → become-driver → vehicle/carte grise → approval → publish → edit permitted details.
3. Passenger request → driver review/accept → explicitly authorized wallet payment → confirmed chat → reminder → start/complete → mutual rating/comment.
4. Passenger and driver cancellation at each allowed state → exact seat/refund/promo changes → notification → no duplicate effects on retry.
5. Flouci sandbox top-up → verified provider settlement → paginated history → cancelled/failed/pending return states remain truthful.
6. Welcome eligibility → one passenger credit and eligible driver fuel entitlement → concurrent redemption/replayed approval cannot duplicate them.
7. Saved route → eligible route-primary/shared-interest match → notification → cancellation/reschedule invalidates stale suggestions.
8. Report eligible misconduct → staff review/suspension through backend or Django admin → audit record → restricted API/chat access.
9. Brand authentication → owned funded campaign → consented audience/sponsored ride/offer → engagement and paid redemption → reconciled owner-only analytics.

A phase is complete only when its business path passes against the real backend, its unit/integration/regression tests pass, generated contracts agree, and UI behavior is verified on the relevant platform. Record evidence and open limitations before advancing; mocks, static types, or screenshots alone are not acceptance evidence.

## 8. Gap-to-phase coverage

| Supplied gap | Delivery phase(s) |
| --- | --- |
| Mock-only runtime and integration response issues | 0 |
| Phone/email signup, personal information, editable profile, avatar | 1 |
| CIN/carte grise, driver onboarding, verification review/more documents | 1 |
| Ride notes, edit/cancel/start/complete, safe booking payment/cancellation policy | 2 |
| Departure + destination search, date/time/price/seats, comparable cards, map | 3 |
| Driver My rides, passenger requests, schedule/profile summaries | 3 |
| Real personal interests, shared counts, preferred interests/atmosphere | 4 |
| Saved routes and route-primary/social ranking regression | 0, 4, 6 |
| Top-up/full history/Flouci, payment visibility and driver earnings | 5 |
| Passenger 10 TND welcome credit, driver 10 TND fuel reward, promo eligibility | 5 |
| Bank/direct payments | 5, external provider/reconciliation gate |
| Missing event handlers, inbox, push/SMS/email, reminders | 1–2 producer foundations, 6 completion |
| Confirmed-booking chat and restricted-account enforcement | 6–7 |
| Public-safe ratings/profiles/counts/comments, report validation | 3, 7 |
| Staff suspension, verification/moderation/ride investigation, audit/report integrity | 1, 7; backend/Django admin only |
| Brand role/portal, interest targeting, sponsored product offers, analytics/promo tracking | 8 |
| Existing API bugs incorrectly alleged: missing pagination, saved-route DELETE | 0 regression verification; no speculative fix |
| Administrator dashboard/frontend reporting screens | Explicitly excluded |
| Cross-platform, migration, regression and complete-product validation | Every phase, final gate 9 |

## 9. First implementation slice

Start with Phase 0 only: run the existing stack and regression tests, prove real phone authentication, fix the ride-create response if reproduced, protect cash crediting, turn off mocks in the development integration environment, and verify existing ride/request/wallet/saved-route paths. Record the actual breakage and results before Phase 1.

This roadmap does not authorize publication, production payment activity, enabling real identity uploads, changing shared permissions, destructive migrations, or deploying to shared infrastructure. Obtain the appropriate approval for those operations when implementation reaches them.
