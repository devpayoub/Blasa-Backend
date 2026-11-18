from django.conf import settings
from django.db import models

from core.models import TimeStampedModel

from .constraints import ImmutableQuerySet


class Account(TimeStampedModel):
    """A ledger account: user wallets plus system accounts (revenue, clearing, sponsor pool)."""

    class Kind(models.TextChoices):
        USER = "USER", "User wallet"
        SYSTEM = "SYSTEM", "System account"

    kind = models.CharField(max_length=10, choices=Kind.choices)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="ledger_accounts"
    )
    code = models.CharField(max_length=100, unique=True)  # e.g. "user:<uuid>", "sys:platform_revenue", "sponsor:campaign:<uuid>"
    currency = models.CharField(max_length=3, default="TND")
    allow_negative = models.BooleanField(default=False)

    def __str__(self):
        return self.code


class Transaction(TimeStampedModel):
    """Groups ledger entries that must balance together."""

    kind = models.CharField(max_length=40)  # e.g. RIDE_PAYMENT, TOPUP, REFUND, SPONSOR_SUBSIDY
    reference = models.CharField(max_length=120, unique=True)  # Idempotency key
    description = models.CharField(max_length=255, blank=True)


class LedgerEntry(TimeStampedModel):
    transaction = models.ForeignKey(Transaction, on_delete=models.PROTECT, related_name="entries")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="entries")
    amount = models.DecimalField(max_digits=14, decimal_places=3)  # Positive = credit, negative = debit (millimes)

    objects = ImmutableQuerySet.as_manager()

    class Meta:
        indexes = [models.Index(fields=["account", "created_at"])]

    def save(self, *args, **kwargs):
        if self._state.adding is False:
            raise PermissionError("Ledger entries are immutable")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Ledger entries are immutable")
