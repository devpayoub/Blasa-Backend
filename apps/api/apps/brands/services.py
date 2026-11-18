"""Sponsor credits flow through the wallet.

A campaign gets its own sponsor account (funded from verified payments or a
staff-reconciled transfer, never from a client-declared amount). When a booking
on a sponsored ride is confirmed, a SPONSOR_SUBSIDY transaction pays the
passenger's discount out of that account, so the ledger always shows the
sponsor leg.

Brand access is scoped by ownership: every campaign read or write resolves
through `owned_campaigns(user)`, which only ever returns campaigns of the brand
the caller owns. Staff keep their own surface for reconciliation.
"""

from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.db.models import Count, Sum
from django.utils import timezone

from apps.audit.services import record_action
from apps.wallet.models import Account, LedgerEntry
from apps.wallet.services import (
    balance_of,
    ensure_user_account,
    get_system_account,
    legs_of,
    post_transaction,
    reverse_transaction,
    transaction_exists,
)

from .models import Brand, Campaign, CampaignEvent

CLEARING_ACCOUNT = "sys:gateway_clearing"
MILLI = Decimal("0.001")
MAX_FUNDING = Decimal("100000.000")


class CampaignError(ValueError):
    """Domain rule violation; the view layer maps this to HTTP 400."""


class NotBrandOwner(PermissionError):
    """The caller does not own this brand; the view layer maps this to HTTP 403."""


# ------------------------------------------------------------------ ownership


def owned_brands(user):
    """Brands the caller may operate. Staff see all; a BRAND account sees its own."""
    if user is None or not getattr(user, "is_authenticated", False):
        return Brand.objects.none()
    if user.is_staff:
        return Brand.objects.all()
    return Brand.objects.filter(owner=user)


def owned_campaigns(user):
    return Campaign.objects.filter(brand__in=owned_brands(user))


def assert_brand_owner(brand: Brand, user) -> None:
    if not (user.is_staff or brand.owner_id == getattr(user, "pk", None)):
        raise NotBrandOwner("This brand belongs to another account")


def assert_campaign_owner(campaign: Campaign, user) -> None:
    assert_brand_owner(campaign.brand, user)


def brand_of(user) -> Brand | None:
    return owned_brands(user).select_related("owner").first()


# ------------------------------------------------------------------- accounts


def campaign_account(campaign: Campaign) -> Account:
    """One wallet account per campaign; spending beyond its balance is refused."""
    account, _ = Account.objects.get_or_create(
        kind=Account.Kind.SYSTEM,
        code=f"sponsor:campaign:{campaign.pk}",
        defaults={"currency": "TND", "allow_negative": False},
    )
    return account


@transaction.atomic
def fund_campaign(*, campaign: Campaign, amount: Decimal, reference: str | None = None) -> Account:
    """Staff-reconciled funding: move sponsor credits from gateway clearing.

    Only staff reach this path. Brands fund through a verified payment intent.
    """
    if amount is None or amount <= 0:
        raise CampaignError("Funding amount must be positive")
    if amount > MAX_FUNDING:
        raise CampaignError("Funding amount is above the supported maximum")
    clearing = get_system_account(CLEARING_ACCOUNT)
    sponsor = campaign_account(campaign)
    post_transaction(
        kind="SPONSOR_TOPUP",
        reference=reference or f"sponsor_topup:{campaign.pk}:{timezone.now().timestamp()}",
        legs=[(clearing, -amount), (sponsor, amount)],
        description=f"Sponsor funding for campaign {campaign.pk}",
    )
    return sponsor


def record_funding(*, campaign: Campaign, intent) -> None:
    """Called inside settlement.

    `budget` stays the sponsor's declared cap; the money that backs it lives in
    the campaign account, so a brand can never spend beyond what it actually paid.
    """
    record_action(
        actor=intent.user, action="brand.campaign_funded", object_type="campaign", object_id=campaign.pk,
        metadata={"intent": str(intent.pk), "amount": str(intent.amount), "gateway": intent.gateway},
    )


def create_funding_intent(*, campaign: Campaign, user, amount: Decimal):
    """Brand-initiated funding through the payment provider.

    Deliberately refuses the cash gateway: a brand may never create spendable
    credit without a verified provider settlement.
    """
    from apps.payments.models import PaymentIntent
    from apps.payments.services import GatewayError, get_gateway

    if amount is None:
        raise CampaignError("A funding amount is required")
    try:
        amount = Decimal(str(amount)).quantize(MILLI)
    except (InvalidOperation, ValueError):
        raise CampaignError("The funding amount must be a decimal number") from None
    if amount <= 0:
        raise CampaignError("Funding amount must be positive")
    if amount > MAX_FUNDING:
        raise CampaignError("Funding amount is above the supported maximum")
    if not campaign.is_active:
        raise CampaignError("Paused campaigns cannot be funded")

    intent = PaymentIntent.objects.create(
        user=user, kind=PaymentIntent.Kind.CAMPAIGN_FUNDING, campaign=campaign,
        gateway=PaymentIntent.Gateway.FLOUCI, amount=amount,
    )
    try:
        data = get_gateway(PaymentIntent.Gateway.FLOUCI).create_intent(intent)
    except GatewayError as exc:
        with transaction.atomic():
            intent.status = PaymentIntent.Status.FAILED
            intent.save(update_fields=["status", "updated_at"])
        raise CampaignError(f"The payment provider refused this funding: {exc}") from exc
    return intent, data


def campaign_balance(campaign: Campaign) -> Decimal:
    return balance_of(campaign_account(campaign))


# ------------------------------------------------------------------- matching


def campaign_for_ride(ride_id) -> Campaign | None:
    """The live campaign sponsoring this ride, if any (active + inside window).

    Deterministic: the most recently created live campaign wins, so a ride with
    two sponsorships always resolves the same way.
    """
    for campaign in Campaign.objects.filter(sponsored_rides__ride_id=ride_id).order_by("-created_at", "-pk"):
        if campaign.covers():
            return campaign
    return None


def sponsorship_of(ride_id) -> Campaign | None:
    """The campaign to display for a ride, even when it is paused or out of window.

    Labels stay visible so a passenger is never shown a discount that will not apply.
    """
    return Campaign.objects.filter(sponsored_rides__ride_id=ride_id).order_by("-created_at", "-pk").first()


def _discount_for(campaign: Campaign, booking) -> Decimal:
    return min(campaign.discount_per_seat * booking.seats, booking.fare_amount).quantize(MILLI)


def _eligible(campaign: Campaign, booking) -> bool:
    return campaign.covers() and campaign.audience_matches(booking.passenger)


def quote_subsidy(booking) -> Decimal:
    """What subsidy_for_booking would grant right now, without posting anything."""
    existing = _subsidy_posted(booking)
    if existing is not None:
        return existing
    campaign = campaign_for_ride(booking.ride_id)
    if campaign is None or not _eligible(campaign, booking):
        return Decimal("0.000")
    discount = _discount_for(campaign, booking)
    if discount <= 0 or campaign.spent + discount > campaign.budget:
        return Decimal("0.000")
    return discount


def _subsidy_posted(booking) -> Decimal | None:
    for _, amount in legs_of(f"sponsor_subsidy:{booking.id}"):
        if amount > 0:
            return amount
    return None


@transaction.atomic
def subsidy_for_booking(booking) -> Decimal | None:
    """Pay the sponsor's share for a confirmed booking on a sponsored ride.

    Returns the discount granted, or None when the ride has no live campaign (or
    the campaign budget ran out — the booking then pays full fare). Raises
    wallet.InsufficientFunds (mapped to 409 by the booking view) when the
    sponsor account balance cannot cover the discount. Retries return the
    original discount without touching the spent counter again.
    """
    existing = _subsidy_posted(booking)
    if existing is not None:
        return existing
    campaign = campaign_for_ride(booking.ride_id)
    if campaign is None or not _eligible(campaign, booking):
        return None

    discount = _discount_for(campaign, booking)
    if discount <= 0:
        return None

    # Row-lock the campaign so concurrent confirms cannot exceed the budget.
    locked = Campaign.objects.select_for_update().get(pk=campaign.pk)
    if locked.spent + discount > locked.budget:
        return None  # budget exhausted — the booking proceeds at full fare

    sponsor = campaign_account(locked)
    passenger = ensure_user_account(booking.passenger)
    post_transaction(
        kind="SPONSOR_SUBSIDY",
        reference=f"sponsor_subsidy:{booking.id}",
        legs=[(sponsor, -discount), (passenger, discount)],
        description=f"Sponsor subsidy for booking {booking.id}",
    )
    locked.spent += discount
    locked.save(update_fields=["spent", "updated_at"])
    record_event(
        campaign=locked, kind=CampaignEvent.Kind.REDEMPTION, ride=booking.ride, user=booking.passenger,
        amount=discount, dedupe_key=f"subsidy:{booking.id}",
        metadata={"booking": str(booking.id), "source": "sponsor_subsidy"},
    )
    return discount


@transaction.atomic
def reverse_subsidy_for_booking(booking) -> Decimal:
    """Return the subsidy to the campaign that originally paid it. Idempotent by booking id."""
    reference = f"sponsor_subsidy_reversal:{booking.id}"
    if transaction_exists(reference):
        return Decimal("0.000")
    legs = legs_of(f"sponsor_subsidy:{booking.id}")
    if not legs:
        return Decimal("0.000")

    sponsor = next(account for account, amount in legs if amount < 0)
    discount = next(amount for _, amount in legs if amount > 0)
    campaign_pk = sponsor.code.removeprefix("sponsor:campaign:")
    locked = Campaign.objects.select_for_update().get(pk=campaign_pk)
    reverse_transaction(
        original=f"sponsor_subsidy:{booking.id}",
        reference=reference,
        kind="SPONSOR_SUBSIDY_REVERSAL",
        description=f"Sponsor subsidy reversal for booking {booking.id}",
    )
    locked.spent = max(locked.spent - discount, Decimal("0"))
    locked.save(update_fields=["spent", "updated_at"])
    # A reversal removes the redemption it paid for, so analytics match the ledger.
    CampaignEvent.objects.filter(campaign=locked, dedupe_key=f"subsidy:{booking.id}").delete()
    return discount


def campaign_spent_total(campaign: Campaign) -> Decimal:
    """Authoritative spend: subsidies paid out of this campaign minus subsidies returned to it."""
    total = LedgerEntry.objects.filter(
        account=campaign_account(campaign),
        transaction__kind__in=["SPONSOR_SUBSIDY", "SPONSOR_SUBSIDY_REVERSAL"],
    ).aggregate(total=Sum("amount"))["total"]
    return -(total or Decimal("0"))


# -------------------------------------------------------------------- events


def record_event(
    *, campaign: Campaign, kind: str, dedupe_key: str, ride=None, user=None,
    amount: Decimal | None = None, metadata: dict | None = None,
) -> CampaignEvent | None:
    """Idempotent telemetry write. A replayed key returns None and changes nothing."""
    if kind not in CampaignEvent.Kind.values:
        raise CampaignError("Unknown campaign event kind")
    event, created = CampaignEvent.objects.get_or_create(
        campaign=campaign, dedupe_key=dedupe_key[:120],
        defaults={
            "kind": kind, "ride": ride, "user": user,
            "amount": (amount or Decimal("0.000")).quantize(MILLI), "metadata": metadata or {},
        },
    )
    return event if created else None


def campaign_analytics(campaign: Campaign) -> dict:
    """Aggregate owner-facing numbers. Never names individual members or their interests."""
    counts = dict(
        CampaignEvent.objects.filter(campaign=campaign).values_list("kind").annotate(n=Count("id"))
    )
    promo_value = CampaignEvent.objects.filter(
        campaign=campaign, kind=CampaignEvent.Kind.REDEMPTION,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    granted = CampaignEvent.objects.filter(
        campaign=campaign, kind=CampaignEvent.Kind.GRANT,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    return {
        "campaign": str(campaign.id),
        "impressions": counts.get(CampaignEvent.Kind.IMPRESSION, 0),
        "engagements": counts.get(CampaignEvent.Kind.ENGAGEMENT, 0),
        "redemptions": counts.get(CampaignEvent.Kind.REDEMPTION, 0),
        "promo_codes_issued": counts.get(CampaignEvent.Kind.GRANT, 0),
        "subsidy_spend": campaign_spent_total(campaign),
        "promo_redemption_value": promo_value,
        "promo_value_outstanding": max(granted - promo_value, Decimal("0")),
        "budget": campaign.budget,
        "budget_remaining": max(campaign.budget - campaign.spent, Decimal("0")),
        "balance": campaign_balance(campaign),
        "audience_tags": campaign.audience_interests.count(),
    }


# ---------------------------------------------------------------- promo codes


@transaction.atomic
def issue_promo_code(*, campaign: Campaign, amount: Decimal, expires_at=None, actor=None):
    """Create an open promo code funded by this campaign's identity.

    The code is single-use (the voucher service enforces it) and its redemption
    is recorded against the campaign, so brands see promo performance separate
    from ride subsidies. Issuing does not move money: the incentive pool pays at
    redemption and the event carries the provenance.
    """
    import uuid

    from apps.incentives.models import Voucher

    try:
        amount = Decimal(str(amount)).quantize(MILLI)
    except (InvalidOperation, ValueError):
        raise CampaignError("The promo amount must be a decimal number") from None
    if amount <= 0:
        raise CampaignError("Promo amount must be positive")
    if amount > campaign.discount_per_seat * 10:
        raise CampaignError("A promo code cannot exceed ten seats' worth of discount")
    outstanding = CampaignEvent.objects.filter(
        campaign=campaign, kind=CampaignEvent.Kind.GRANT,
    ).aggregate(total=Sum("amount"))["total"] or Decimal("0")
    if outstanding + amount > campaign.budget:
        raise CampaignError("This promo code would exceed the campaign budget")

    voucher = Voucher.objects.create(
        code=f"BRAND{uuid.uuid4().hex[:8].upper()}", amount=amount, campaign=campaign,
        expires_at=expires_at,
    )
    record_event(
        campaign=campaign, kind=CampaignEvent.Kind.GRANT, dedupe_key=f"grant:{voucher.pk}",
        amount=amount, metadata={"voucher": str(voucher.pk), "code": voucher.code},
    )
    if actor is not None:
        record_action(
            actor=actor, action="brand.promo_issued", object_type="campaign", object_id=campaign.pk,
            metadata={"voucher": str(voucher.pk), "amount": str(amount)},
        )
    return voucher


def record_promo_redemption(*, voucher, booking) -> None:
    """Provenance hook called by the voucher service when a campaign code is used."""
    if voucher.campaign_id is None:
        return
    record_event(
        campaign=voucher.campaign, kind=CampaignEvent.Kind.REDEMPTION, ride=booking.ride, user=booking.passenger,
        amount=voucher.amount, dedupe_key=f"promo:{booking.id}",
        metadata={"voucher": str(voucher.pk), "booking": str(booking.id), "source": "promo_code"},
    )


def campaign_rides(campaign: Campaign):
    return campaign.sponsored_rides.select_related("ride").all()


def sponsor_summary_for_ride(ride_id) -> dict | None:
    """Public-safe sponsored content attached to a ride card or detail page."""
    campaign = sponsorship_of(ride_id)
    if campaign is None:
        return None
    return {
        "campaign": str(campaign.pk),
        "brand_name": campaign.brand.name,
        "discount_per_seat": campaign.discount_per_seat,
        "label": "Sponsored",
        "title": campaign.promotion_title,
        "body": campaign.promotion_body,
        "url": campaign.promotion_url,
        "active": campaign.covers(),
    }
