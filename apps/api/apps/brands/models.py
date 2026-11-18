from django.conf import settings
from django.db import models
from django.utils import timezone

from core.models import TimeStampedModel


class Brand(TimeStampedModel):
    """A sponsor organisation. `owner` is the BRAND account allowed to run its campaigns."""

    name = models.CharField(max_length=100, unique=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="brands"
    )
    website = models.URLField(blank=True)
    logo_key = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["name"]
        constraints = [
            # One BRAND account owns one brand: the ownership boundary the portal scopes on.
            models.UniqueConstraint(fields=["owner"], name="brand_one_per_owner"),
        ]

    def __str__(self):
        return self.name


class Campaign(TimeStampedModel):
    """A sponsor budget that discounts passenger fares on selected rides."""

    brand = models.ForeignKey(Brand, on_delete=models.PROTECT, related_name="campaigns")
    name = models.CharField(max_length=120)
    discount_per_seat = models.DecimalField(max_digits=10, decimal_places=3)
    budget = models.DecimalField(max_digits=14, decimal_places=3)  # Sponsor credits committed
    spent = models.DecimalField(max_digits=14, decimal_places=3, default=0)
    starts_at = models.DateTimeField(null=True, blank=True)
    ends_at = models.DateTimeField(null=True, blank=True)
    is_active = models.BooleanField(default=True)
    paused_at = models.DateTimeField(null=True, blank=True)
    # Declared audience. Empty = every member is eligible. Interest tags only:
    # individual memberships are never exposed to the brand.
    audience_interests = models.ManyToManyField("users.InterestTag", blank=True, related_name="campaigns")
    # Labeled sponsored content shown with the discount.
    promotion_title = models.CharField(max_length=120, blank=True, default="")
    promotion_body = models.CharField(max_length=300, blank=True, default="")
    promotion_url = models.URLField(blank=True, default="")

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["brand", "is_active"])]

    def covers(self, at=None) -> bool:
        """Whether the campaign's active window includes `at` (defaults to now)."""
        at = at or timezone.now()
        if not self.is_active:
            return False
        if self.starts_at and at < self.starts_at:
            return False
        if self.ends_at and at > self.ends_at:
            return False
        return True

    def audience_matches(self, user) -> bool:
        """Eligibility by declared interests. A member with no interests is not excluded
        by a targeted campaign they could never satisfy; only an explicit mismatch excludes."""
        tags = list(self.audience_interests.values_list("slug", flat=True))
        if not tags:
            return True
        profile = getattr(user, "profile", None)
        if profile is None:
            return False
        return profile.interests.filter(slug__in=tags).exists()

    def __str__(self):
        return f"{self.brand}:{self.name}"


class SponsoredRide(TimeStampedModel):
    campaign = models.ForeignKey(Campaign, on_delete=models.PROTECT, related_name="sponsored_rides")
    ride = models.ForeignKey("rides.Ride", on_delete=models.PROTECT, related_name="sponsorships")

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["campaign", "ride"], name="sponsoredride_unique_campaign_ride")
        ]

    def __str__(self):
        return f"{self.campaign_id}→{self.ride_id}"


class CampaignEvent(TimeStampedModel):
    """Deduplicated sponsored-content telemetry.

    Impressions and engagement are client-reported and therefore untrusted for
    money: `dedupe_key` makes a replay a no-op. Redemptions are recorded by the
    server from the ledger-backed voucher flow, never by a client increment.
    """

    class Kind(models.TextChoices):
        IMPRESSION = "IMPRESSION", "Ride card shown"
        ENGAGEMENT = "ENGAGEMENT", "Offer opened"
        GRANT = "GRANT", "Promo code issued"
        REDEMPTION = "REDEMPTION", "Promo code redeemed"

    campaign = models.ForeignKey(Campaign, on_delete=models.PROTECT, related_name="events")
    kind = models.CharField(max_length=12, choices=Kind.choices)
    ride = models.ForeignKey("rides.Ride", null=True, blank=True, on_delete=models.SET_NULL, related_name="+")
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL, related_name="+"
    )
    amount = models.DecimalField(max_digits=12, decimal_places=3, default=0)
    dedupe_key = models.CharField(max_length=120)
    metadata = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["campaign", "dedupe_key"], name="campaign_event_dedupe"),
        ]
        indexes = [models.Index(fields=["campaign", "kind"])]

    def __str__(self):
        return f"{self.campaign_id}:{self.kind}:{self.dedupe_key}"
