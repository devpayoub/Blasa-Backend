"""Vouchers are issued into user accounts and redeemed automatically at booking
confirm: the incentive pool pays the voucher's amount into the passenger's
wallet, so the passenger only needs cash for the remainder of the fare."""

import logging
import uuid
from datetime import timedelta
from decimal import Decimal

from django.db import IntegrityError, models, transaction
from django.utils import timezone

from apps.audit.services import record_action
from apps.users.models import User
from apps.vehicles.models import Vehicle
from apps.verification.models import VerificationDocument
from apps.wallet.models import Account
from apps.wallet.services import (
    InsufficientFunds,
    balance_of,
    ensure_user_account,
    get_system_account,
    legs_of,
    post_transaction,
    reverse_transaction,
    transaction_exists,
)
from core.events import publish_on_commit

from .models import IncentiveAward, IncentiveCampaign, Voucher

logger = logging.getLogger(__name__)

POOL_ACCOUNT = "sys:incentive_pool"
BUDGET_SOURCE = "sys:promotion_budget"
FUEL_RESERVE = "sys:fuel_reserve"
FUEL_PAYABLE = "sys:fuel_partner_payable"
MILLI = Decimal("0.001")


class VoucherError(ValueError):
    """Domain rule violation; the view layer maps this to HTTP 400."""


class IncentiveError(ValueError):
    pass


@transaction.atomic
def issue_voucher(*, user=None, amount: Decimal, expires_at=None, code: str | None = None) -> Voucher:
    if amount is None or amount <= 0:
        raise VoucherError("Voucher amount must be positive")
    return Voucher.objects.create(
        code=code or f"BL-{uuid.uuid4().hex[:10].upper()}",
        amount=amount.quantize(MILLI),
        issued_to=user,
        expires_at=expires_at,
    )


def available_for(user):
    """The caller's live vouchers: not redeemed, not expired, soonest expiry first."""
    now = timezone.now()
    return (
        Voucher.objects.filter(
            models.Q(expires_at__isnull=True) | models.Q(expires_at__gte=now),
            issued_to=user,
            redeemed_at__isnull=True,
        ).order_by("expires_at")
    )


def _voucher_posted(booking) -> Decimal | None:
    for _, amount in legs_of(f"voucher_redeem:{booking.id}"):
        if amount > 0:
            return amount
    return None


def quote_voucher(booking, *, cap: Decimal) -> tuple[Voucher | None, Decimal]:
    """The voucher redeem_voucher_for_booking would apply now, capped at what is left to pay."""
    existing = _voucher_posted(booking)
    if existing is not None:
        return Voucher.objects.filter(redeemed_by_id=booking.id).first(), existing
    voucher = available_for(booking.passenger).first()
    if voucher is None or cap <= 0:
        return None, Decimal("0.000")
    return voucher, min(voucher.amount, cap).quantize(MILLI)


@transaction.atomic
def redeem_voucher_for_booking(booking, *, cap: Decimal) -> Decimal | None:
    """Auto-redeem the passenger's soonest-expiring voucher against this booking at confirm.

    cap is the fare left after any sponsor subsidy, so a voucher never credits
    more than the passenger still owes. Returns the credited amount, or None when
    nothing applies. Idempotent by booking id.
    """
    existing = _voucher_posted(booking)
    if existing is not None:
        return existing
    if cap <= 0:
        return None
    voucher = available_for(booking.passenger).select_for_update().first()
    if voucher is None:
        return None

    amount = min(voucher.amount, cap).quantize(MILLI)
    if amount <= 0:
        return None

    pool = get_system_account(POOL_ACCOUNT)
    passenger = ensure_user_account(booking.passenger)
    post_transaction(
        kind="VOUCHER_REDEEM",
        reference=f"voucher_redeem:{booking.id}",
        legs=[(pool, -amount), (passenger, amount)],
        description=f"Voucher {voucher.code} redeemed on booking {booking.id}",
    )
    voucher.redeemed_at = timezone.now()
    voucher.redeemed_by = booking
    voucher.save(update_fields=["redeemed_at", "redeemed_by", "updated_at"])
    if voucher.campaign_id:
        # Brand promo provenance: the campaign reports this redemption, never the client.
        from apps.brands.services import record_promo_redemption

        record_promo_redemption(voucher=voucher, booking=booking)
    return amount


@transaction.atomic
def reverse_voucher_for_booking(booking) -> Decimal:
    """Give the voucher back, with its original expiry, when a paid booking is cancelled."""
    reference = f"voucher_redeem_reversal:{booking.id}"
    amount = _voucher_posted(booking)
    if amount is None or transaction_exists(reference):
        return Decimal("0.000")
    voucher = Voucher.objects.select_for_update().filter(redeemed_by_id=booking.id).first()
    reverse_transaction(
        original=f"voucher_redeem:{booking.id}",
        reference=reference,
        kind="VOUCHER_REDEEM_REVERSAL",
        description=f"Voucher reversal for booking {booking.id}",
    )
    if voucher is not None:
        voucher.redeemed_at = None
        voucher.redeemed_by = None
        voucher.save(update_fields=["redeemed_at", "redeemed_by", "updated_at"])
    return amount


@transaction.atomic
def claim_voucher(*, user, code: str) -> Voucher:
    """Attach an open (unassigned) voucher code to the caller. Every failure reads the same."""
    if user.role != User.Role.PASSENGER:
        raise VoucherError("Only passenger accounts can use ride vouchers.")
    now = timezone.now()
    voucher = (
        Voucher.objects.select_for_update()
        .filter(models.Q(expires_at__isnull=True) | models.Q(expires_at__gte=now))
        .filter(code=(code or "").strip().upper(), issued_to__isnull=True, redeemed_at__isnull=True)
        .first()
    )
    if voucher is None:
        raise VoucherError("This code is not valid.")
    voucher.issued_to = user
    voucher.save(update_fields=["issued_to", "updated_at"])
    return voucher


def incentive_account(campaign: IncentiveCampaign) -> Account:
    """Each campaign spends only what staff funded into it."""
    account, _ = Account.objects.get_or_create(
        kind=Account.Kind.SYSTEM, code=f"incentive:campaign:{campaign.pk}",
        defaults={"currency": "TND", "allow_negative": False},
    )
    return account


def campaign_balance(campaign: IncentiveCampaign) -> Decimal:
    return balance_of(incentive_account(campaign))


@transaction.atomic
def fund_incentive_campaign(*, campaign: IncentiveCampaign, amount: Decimal, reference: str = "") -> Decimal:
    if amount is None or amount <= 0:
        raise IncentiveError("Funding amount must be positive")
    post_transaction(
        kind="INCENTIVE_FUNDING",
        reference=f"incentive_fund:{campaign.pk}:{reference or uuid.uuid4().hex}",
        legs=[(get_system_account(BUDGET_SOURCE), -amount), (incentive_account(campaign), amount)],
        description=f"Funding for incentive campaign {campaign.name}",
    )
    return campaign_balance(campaign)


def _has_approved(user_id, doc_type) -> bool:
    return VerificationDocument.objects.filter(
        user_id=user_id, doc_type=doc_type, status=VerificationDocument.Status.APPROVED,
    ).exists()


def is_eligible(user: User, kind: str) -> bool:
    if not user.is_active or not _has_approved(user.pk, VerificationDocument.DocType.CIN):
        return False
    if kind == IncentiveCampaign.Kind.WELCOME_CREDIT:
        return user.role == User.Role.PASSENGER
    return user.role == User.Role.DRIVER and Vehicle.objects.filter(
        driver=user, is_active=True, carte_grise__status=VerificationDocument.Status.APPROVED,
    ).exists()


def live_campaign(kind: str, now=None) -> IncentiveCampaign | None:
    now = now or timezone.now()
    for campaign in IncentiveCampaign.objects.filter(kind=kind, is_active=True).order_by("created_at"):
        if campaign.is_live(now):
            return campaign
    return None


def _issue(user: User, campaign: IncentiveCampaign, now) -> IncentiveAward:
    welcome = campaign.kind == IncentiveCampaign.Kind.WELCOME_CREDIT
    prefix = "WELCOME" if welcome else "FUEL"
    award = IncentiveAward.objects.create(
        campaign=campaign, user=user, kind=campaign.kind, amount=campaign.amount,
        code=f"{prefix}-{uuid.uuid4().hex[:10].upper()}",
        expires_at=now + timedelta(days=campaign.award_validity_days),
    )
    # Reserve the budget now so a campaign can never promise more than it holds.
    post_transaction(
        kind="INCENTIVE_RESERVE",
        reference=f"incentive_award:{award.pk}",
        legs=[
            (incentive_account(campaign), -award.amount),
            (get_system_account(POOL_ACCOUNT if welcome else FUEL_RESERVE), award.amount),
        ],
        description=f"{campaign.get_kind_display()} for user {user.pk}",
    )
    if welcome:
        award.voucher = Voucher.objects.create(
            code=award.code, amount=award.amount, issued_to=user, expires_at=award.expires_at,
        )
        award.save(update_fields=["voucher", "updated_at"])
    publish_on_commit("incentive.awarded", award_id=str(award.pk))
    return award


def evaluate_user(user_id) -> list[IncentiveAward]:
    """Grant each reward the user newly qualifies for.

    Runs inside the caller's transaction (a document review, a vehicle save) but in its
    own savepoint: an empty budget or a concurrent grant must never fail that action.
    """
    user = User.objects.filter(pk=user_id).first()
    if user is None:
        return []
    now = timezone.now()
    awarded = []
    for kind in IncentiveCampaign.Kind.values:
        if IncentiveAward.objects.filter(user=user, kind=kind).exists() or not is_eligible(user, kind):
            continue
        campaign = live_campaign(kind, now)
        if campaign is None:
            continue
        try:
            with transaction.atomic():
                awarded.append(_issue(user, campaign, now))
        except InsufficientFunds:
            logger.warning("Incentive campaign %s is out of budget; user %s not awarded", campaign.pk, user.pk)
        except IntegrityError:
            pass  # a concurrent evaluation already granted it
    return awarded


def evaluate_campaign(campaign: IncentiveCampaign) -> int:
    """Backfill a newly activated campaign for users who qualified before it went live."""
    if not campaign.is_live(timezone.now()):
        raise IncentiveError("Only a live campaign can be evaluated")
    role = User.Role.PASSENGER if campaign.kind == IncentiveCampaign.Kind.WELCOME_CREDIT else User.Role.DRIVER
    candidates = (
        User.objects.filter(
            role=role, is_active=True,
            documents__doc_type=VerificationDocument.DocType.CIN,
            documents__status=VerificationDocument.Status.APPROVED,
        )
        .exclude(incentive_awards__kind=campaign.kind)
        .distinct()
        .values_list("pk", flat=True)
    )
    return sum(len(evaluate_user(pk)) for pk in candidates)


def handle_eligibility_event(*, user_id=None, driver_id=None, **_):
    evaluate_user(user_id or driver_id)


def award_status(award: IncentiveAward, now=None) -> str:
    now = now or timezone.now()
    if award.status == IncentiveAward.Status.ISSUED:
        if award.voucher_id and award.voucher.redeemed_at:
            return IncentiveAward.Status.REDEEMED
        if award.expires_at <= now:
            return "EXPIRED"
    return award.status


@transaction.atomic
def redeem_fuel_award(*, code: str, staff, note: str = "") -> IncentiveAward:
    """Staff record that a fuel partner honoured the code. Only one redemption can ever succeed."""
    award = (
        IncentiveAward.objects.select_for_update()
        .filter(code=(code or "").strip().upper(), kind=IncentiveCampaign.Kind.DRIVER_FUEL)
        .first()
    )
    if award is None:
        raise IncentiveError("No fuel reward has this code")
    status = award_status(award)
    if status != IncentiveAward.Status.ISSUED:
        raise IncentiveError(f"This fuel reward is {status.lower()}")
    post_transaction(
        kind="FUEL_REDEMPTION",
        reference=f"fuel_redeem:{award.pk}",
        legs=[(get_system_account(FUEL_RESERVE), -award.amount), (get_system_account(FUEL_PAYABLE), award.amount)],
        description=f"Fuel reward {award.code} redeemed",
    )
    award.status = IncentiveAward.Status.REDEEMED
    award.redeemed_at = timezone.now()
    award.redeemed_by = staff
    award.note = note[:255]
    award.save(update_fields=["status", "redeemed_at", "redeemed_by", "note", "updated_at"])
    record_action(
        actor=staff, action="incentive.fuel_redeemed", object_type="incentive_award", object_id=award.pk,
        reason=note, metadata={"code": award.code, "amount": str(award.amount), "campaign_id": str(award.campaign_id),
                               "driver_id": str(award.user_id)},
    )
    return award
