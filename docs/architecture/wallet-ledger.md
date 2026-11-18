# Wallet ledger (ADR)

## Model

Double-entry, append-only ledger in `apps/api/apps/wallet/`:

- `Account` — user wallets (`kind=USER`) and system accounts (`kind=SYSTEM`: platform revenue, clearing, sponsor pool). `allow_negative` permits overdraw only for designated system accounts.
- `Transaction` — groups legs that must balance; `reference` is a unique idempotency key.
- `LedgerEntry` — one row per leg; positive = credit, negative = debit; amounts in millimes (3 decimals).

## Invariants

1. **Balanced.** `post_transaction` rejects legs that do not sum to zero.
2. **Append-only.** `LedgerEntry` uses `ImmutableQuerySet` (blocks queryset `update`/`delete`), blocks re-`save()` of persisted rows, and blocks `delete()`. The Django admin exposes read-only views.
3. **Idempotent.** Posting the same `reference` twice returns the existing transaction; a unique constraint is the backstop.
4. **No negative balances.** Debits are checked against the derived balance inside the same transaction, after locking all involved accounts in primary-key order (deadlock avoidance).
5. **Derived balances.** A balance is always `Sum("amount")` over an account's entries — never a stored column.

## Operational flow (planned, Phase 4)

Booking confirmation posts `RIDE_PAYMENT`: passenger debit → driver credit − platform fee, with `reference = "booking:<id>"`. TOPUPs arrive through gateway webhooks; REFUNDs reverse failed/cancelled rides. Sponsor subsidies (`SPONSOR_SUBSIDY`) add a third leg from the brand pool.
