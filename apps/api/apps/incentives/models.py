from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


class Voucher(TimeStampedModel):
    """A credit issued into a user's account, redeemable at booking confirm.

    `issued_to` may be null for an open code that the first eligible user
    redeems; a redeemed voucher records which booking consumed it.
    """

    code = models.CharField(max_length=40, unique=True)
    amount = models.DecimalField(max_digits=10, decimal_places=3)
    issued_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="vouchers"
    )
    # Provenance: a brand-issued promo code reports its redemption to this campaign.
    campaign = models.ForeignKey(
        "brands.Campaign", null=True, blank=True, on_delete=models.PROTECT, related_name="promo_codes"
    )
    expires_at = models.DateTimeField(null=True, blank=True)
    redeemed_by = models.ForeignKey(
        "bookings.Booking", null=True, blank=True, on_delete=models.SET_NULL, related_name="vouchers"
    )
    redeemed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"voucher:{self.code}"


class IncentiveCampaign(TimeStampedModel):
    """A staff-run, pre-funded reward programme. Awards reserve budget from its ledger account."""

    class Kind(models.TextChoices):
        WELCOME_CREDIT = "WELCOME_CREDIT", "Passenger welcome credit"
        DRIVER_FUEL = "DRIVER_FUEL", "Driver fuel reward"

    name = models.CharField(max_length=120)
    kind = models.CharField(max_length=20, choices=Kind.choices)
    amount = models.DecimalField(max_digits=10, decimal_places=3, default="10.000")
    is_active = models.BooleanField(default=False)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    award_validity_days = models.PositiveIntegerField(default=90)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return self.name

    def is_live(self, now) -> bool:
        return (
            self.is_active
            and (self.starts_at is None or self.starts_at <= now)
            and (self.ends_at is None or now < self.ends_at)
        )


class IncentiveAward(TimeStampedModel):
    """One reward per user per kind, ever. Welcome credit is delivered as a booking voucher;
    a fuel reward is a code redeemed at a fuel partner (manually by staff until one is contracted)."""

    class Status(models.TextChoices):
        ISSUED = "ISSUED", "Issued"
        REDEEMED = "REDEEMED", "Redeemed"
        REVOKED = "REVOKED", "Revoked"

    campaign = models.ForeignKey(IncentiveCampaign, on_delete=models.PROTECT, related_name="awards")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="incentive_awards")
    kind = models.CharField(max_length=20, choices=IncentiveCampaign.Kind.choices)
    amount = models.DecimalField(max_digits=10, decimal_places=3)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.ISSUED)
    code = models.CharField(max_length=40, unique=True)
    expires_at = models.DateTimeField()
    voucher = models.OneToOneField(Voucher, null=True, blank=True, on_delete=models.PROTECT, related_name="award")
    redeemed_at = models.DateTimeField(null=True, blank=True)
    redeemed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
    )
    note = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [models.UniqueConstraint(fields=["user", "kind"], name="one_award_per_user_and_kind")]


# Importable alias for SPECTACULAR_SETTINGS.ENUM_NAME_OVERRIDES (stable schema names).
INCENTIVE_AWARD_KIND_CHOICES = IncentiveCampaign.Kind.choices
