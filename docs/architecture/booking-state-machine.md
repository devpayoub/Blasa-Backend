# Booking state machine (ADR)

## States

`REQUESTED → ACCEPTED → CONFIRMED → COMPLETED`

Terminal states: `REJECTED`, `CANCELLED_BY_PASSENGER`, `CANCELLED_BY_DRIVER`, `NO_SHOW`.

## Transition table

Source: `apps/api/apps/bookings/state_machine.py` — the single source of truth.

| From | Action | To |
|---|---|---|
| REQUESTED | accept | ACCEPTED |
| REQUESTED | reject | REJECTED |
| REQUESTED | cancel_passenger | CANCELLED_BY_PASSENGER |
| ACCEPTED | confirm | CONFIRMED |
| ACCEPTED | cancel_passenger | CANCELLED_BY_PASSENGER |
| ACCEPTED | cancel_driver | CANCELLED_BY_DRIVER |
| CONFIRMED | complete | COMPLETED |
| CONFIRMED | no_show | NO_SHOW |
| CONFIRMED | cancel_passenger | CANCELLED_BY_PASSENGER |
| CONFIRMED | cancel_driver | CANCELLED_BY_DRIVER |

## Rules

1. Only transitions listed in `TRANSITIONS` are allowed; everything else raises `InvalidTransition` (surfaced as HTTP 409 by the view layer).
2. Terminal states reject every action.
3. Every transition is applied under a row lock (`select_for_update`) in `bookings.services.apply_action` and writes a `BookingEvent` (from, to, action, actor) plus a `booking.status_changed` domain event.
4. Seats are coupled to the ride inventory at `accept` (reserve) and returned on `reject` / `cancel_*` via `rides.services.release_seats` (wiring scheduled in Phase 2 of the build plan).
