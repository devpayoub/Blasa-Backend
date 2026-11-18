from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from apps.wallet.models import LedgerEntry
from apps.wallet.services import (
    ensure_user_account,
    get_system_account,
    legs_of,
    post_transaction,
    reverse_transaction,
)
from core.events import publish_on_commit

from .gateways import GatewayError, get_gateway
from .models import PaymentIntent, WebhookEvent

# Platform commission on ride fares. Millimes precision (3 decimals).
FEE_RATE = Decimal("0.100")
MILLI = Decimal("0.001")

CLEARING_ACCOUNT = "sys:gateway_clearing"
REVENUE_ACCOUNT = "sys:platform_revenue"


class TopupFailed(Exception):
    """The gateway refused to start the payment; the intent is kept as FAILED for the user to see."""

    def __init__(self, intent: PaymentIntent, message: str):
        super().__init__(message)
        self.intent = intent


class IntentStateError(Exception):
    pass


def create_topup(*, user, amount: Decimal, gateway: str) -> tuple[PaymentIntent, dict]:
    """Create a topup intent and route it through the gateway. Cash settles immediately."""
    if amount is None or amount <= 0:
        raise ValueError("Topup amount must be positive")
    intent = PaymentIntent.objects.create(user=user, kind=PaymentIntent.Kind.TOPUP, gateway=gateway, amount=amount)
    try:
        data = get_gateway(gateway).create_intent(intent)
    except GatewayError as exc:
        with transaction.atomic():
            intent.status = PaymentIntent.Status.FAILED
            intent.save(update_fields=["status", "updated_at"])
            publish_on_commit("payment.status_changed", intent_id=str(intent.pk), status=intent.status)
        raise TopupFailed(intent, str(exc)) from exc
    if gateway == PaymentIntent.Gateway.CASH:
        settle_topup(intent=intent)
        intent.refresh_from_db()
        data = {**data, "status": intent.status}
    return intent, data


@transaction.atomic
def settle_topup(*, intent: PaymentIntent) -> bool:
    """Credit the destination account from gateway clearing, once per intent.

    A trusted success is authoritative even after the intent was marked failed or
    cancelled locally: the money did arrive, so it must be credited.

    A wallet topup credits the user; a campaign-funding intent credits the
    campaign's sponsor account, so brand credit is only ever created by a
    verified provider payment.
    """
    intent = PaymentIntent.objects.select_for_update().get(pk=intent.pk)
    if intent.status == PaymentIntent.Status.PAID:
        return False
    if intent.kind == PaymentIntent.Kind.CAMPAIGN_FUNDING:
        from apps.brands.services import campaign_account, record_funding

        if intent.campaign_id is None:
            raise IntentStateError("A campaign funding intent must name its campaign")
        account = campaign_account(intent.campaign)
        description = f"Brand funding for campaign {intent.campaign_id} via {intent.gateway}"
        reference = f"campaign_funding:{intent.id}"
    else:
        account = ensure_user_account(intent.user)
        description = f"Topup via {intent.gateway}"
        reference = f"topup:{intent.id}"
    clearing = get_system_account(CLEARING_ACCOUNT)
    post_transaction(
        kind="TOPUP" if intent.kind == PaymentIntent.Kind.TOPUP else "SPONSOR_TOPUP",
        reference=reference,
        legs=[(clearing, -intent.amount), (account, intent.amount)],
        description=description,
    )
    intent.status = PaymentIntent.Status.PAID
    intent.save(update_fields=["status", "updated_at"])
    if intent.kind == PaymentIntent.Kind.CAMPAIGN_FUNDING:
        record_funding(campaign=intent.campaign, intent=intent)
    publish_on_commit("payment.status_changed", intent_id=str(intent.pk), status=intent.status)
    return True


@transaction.atomic
def cancel_intent(*, intent: PaymentIntent) -> PaymentIntent:
    intent = PaymentIntent.objects.select_for_update().get(pk=intent.pk)
    if intent.status != PaymentIntent.Status.PENDING:
        raise IntentStateError(f"A {intent.get_status_display().lower()} payment cannot be cancelled")
    intent.status = PaymentIntent.Status.CANCELLED
    intent.save(update_fields=["status", "updated_at"])
    publish_on_commit("payment.status_changed", intent_id=str(intent.pk), status=intent.status)
    return intent


def settle_booking_payment(*, booking, fare: Decimal) -> None:
    """Passenger debit -> driver credit minus platform fee. Idempotent by booking id.

    Called inside the booking transition transaction: a failed settlement rolls the
    whole transition back, so a booking can never be CONFIRMED without being paid.
    """
    passenger = ensure_user_account(booking.passenger)
    driver = ensure_user_account(booking.ride.driver)
    revenue = get_system_account(REVENUE_ACCOUNT)
    fee = (fare * FEE_RATE).quantize(MILLI)
    post_transaction(
        kind="RIDE_PAYMENT",
        reference=f"ride_payment:{booking.id}",
        legs=[(passenger, -fare), (driver, fare - fee), (revenue, fee)],
        description=f"Ride payment for booking {booking.id}",
    )


def refund_booking_payment(*, booking) -> Decimal:
    """Reverse the original ride payment legs exactly. Idempotent by booking id.

    Returns the fare returned to the passenger (0 when the booking was never paid).
    A driver who already spent the fare may go below zero for this posting only:
    the negative balance is a recorded debt, the account is not opened to overdraft.
    """
    original = f"ride_payment:{booking.id}"
    legs = legs_of(original)
    if not legs:
        return Decimal("0.000")
    driver = ensure_user_account(booking.ride.driver)
    reverse_transaction(
        original=original,
        reference=f"refund:{booking.id}",
        kind="REFUND",
        description=f"Refund for booking {booking.id}",
        overdraft=frozenset({driver.pk}),
    )
    passenger = ensure_user_account(booking.passenger)
    return -sum(amount for account, amount in legs if account.pk == passenger.pk)


def _amount_matches(intent: PaymentIntent, parsed: dict) -> bool:
    try:
        reported = Decimal(str(parsed.get("amount")))
    except (InvalidOperation, ValueError):
        return False
    currency = parsed.get("currency")
    return reported == intent.amount and (not currency or currency == intent.currency)


_FAILURE_EVENTS = {
    "payment.failed": PaymentIntent.Status.FAILED,
    "payment.cancelled": PaymentIntent.Status.CANCELLED,
}


def _apply_event(parsed: dict) -> str:
    intent = (
        PaymentIntent.objects.select_for_update()
        .filter(gateway_reference=parsed["reference"], kind__in=[
            PaymentIntent.Kind.TOPUP, PaymentIntent.Kind.CAMPAIGN_FUNDING,
        ])
        .first()
    )
    if intent is None:
        return "unknown_intent"
    if parsed["event"] == "payment.succeeded":
        if not _amount_matches(intent, parsed):
            return "amount_mismatch"
        return "credited" if settle_topup(intent=intent) else "already_paid"
    if parsed["event"] in _FAILURE_EVENTS:
        if intent.status != PaymentIntent.Status.PENDING:
            return "ignored_not_pending"
        intent.status = _FAILURE_EVENTS[parsed["event"]]
        intent.save(update_fields=["status", "updated_at"])
        publish_on_commit("payment.status_changed", intent_id=str(intent.pk), status=intent.status)
        return f"marked_{intent.status.lower()}"
    return "ignored_event"


@transaction.atomic
def handle_webhook(*, gateway: str, body: bytes, signature: str | None) -> WebhookEvent | None:
    """Verify, record, and process an inbound gateway webhook in one transaction.

    Returns None when the event was already processed. Recording and settlement
    commit together, so a settlement failure leaves no event behind and the
    gateway's retry is processed normally.
    """
    parsed = get_gateway(gateway).parse_webhook(body, signature)
    event, _ = WebhookEvent.objects.select_for_update().get_or_create(
        event_id=parsed["event_id"], defaults={"gateway": gateway, "payload": parsed["payload"]}
    )
    if event.processed_at is not None:
        return None
    event.outcome = _apply_event(parsed)
    event.processed_at = timezone.now()
    event.save(update_fields=["outcome", "processed_at", "updated_at"])
    return event


def _money(value: Decimal) -> str:
    return str(value.quantize(MILLI))


def driver_earnings(user) -> dict:
    """A driver's totals derived only from posted ledger legs, so they always agree with the balance."""
    account = ensure_user_account(user)

    def total(kind, *, platform: bool) -> Decimal:
        legs = LedgerEntry.objects.filter(transaction__kind=kind)
        if platform:
            legs = legs.filter(account__code=REVENUE_ACCOUNT, transaction__entries__account=account)
        else:
            legs = legs.filter(account=account)
        return legs.aggregate(total=Sum("amount"))["total"] or Decimal("0.000")

    received, fee_paid = total("RIDE_PAYMENT", platform=False), total("RIDE_PAYMENT", platform=True)
    clawed_back, fee_returned = -total("REFUND", platform=False), -total("REFUND", platform=True)
    return {
        "currency": account.currency,
        "gross_fares": _money(received + fee_paid),
        "platform_fees": _money(fee_paid - fee_returned),
        "refunded": _money(clawed_back + fee_returned),
        "net_earnings": _money(received - clawed_back),
        "paid_bookings": account.entries.filter(transaction__kind="RIDE_PAYMENT").count(),
        "refunded_bookings": account.entries.filter(transaction__kind="REFUND").count(),
    }
