from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


class PaymentIntent(TimeStampedModel):
    """A money-in request routed through a gateway (Flouci online, or cash in hand)."""

    class Kind(models.TextChoices):
        TOPUP = "TOPUP", "Wallet topup"
        CAMPAIGN_FUNDING = "CAMPAIGN_FUNDING", "Brand campaign funding"

    class Status(models.TextChoices):
        PENDING = "PENDING", "Pending"
        PAID = "PAID", "Paid"
        FAILED = "FAILED", "Failed"
        CANCELLED = "CANCELLED", "Cancelled"

    class Gateway(models.TextChoices):
        FLOUCI = "FLOUCI", "Flouci"
        CASH = "CASH", "Cash"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="payment_intents")
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.TOPUP)
    # Set only for CAMPAIGN_FUNDING: settlement credits this campaign's sponsor account.
    campaign = models.ForeignKey(
        "brands.Campaign", null=True, blank=True, on_delete=models.PROTECT, related_name="funding_intents"
    )
    gateway = models.CharField(max_length=20, choices=Gateway.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=3)
    currency = models.CharField(max_length=3, default="TND")
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING, db_index=True)
    # The reference the gateway assigns; empty until the gateway accepts the intent.
    gateway_reference = models.CharField(max_length=120, blank=True, default="")

    class Meta:
        indexes = [models.Index(fields=["user", "created_at"])]
        constraints = [
            # A funding intent must name its campaign; a wallet topup must not.
            models.CheckConstraint(
                condition=(
                    models.Q(kind="CAMPAIGN_FUNDING", campaign__isnull=False)
                    | models.Q(campaign__isnull=True)
                ),
                name="payment_intent_campaign_matches_kind",
            ),
        ]


class WebhookEvent(TimeStampedModel):
    """Raw inbound gateway notifications. event_id is the webhook idempotency key."""

    gateway = models.CharField(max_length=20)
    event_id = models.CharField(max_length=120, unique=True)
    payload = models.JSONField()
    processed_at = models.DateTimeField(null=True, blank=True)
    # What processing decided (credited, amount_mismatch, unknown_intent, ...), kept for reconciliation.
    outcome = models.CharField(max_length=40, blank=True, default="")
