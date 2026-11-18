from decimal import Decimal

from django.db import IntegrityError, transaction
from django.db.models import Sum

from core.events import publish_on_commit

from .models import Account, LedgerEntry, Transaction


class InsufficientFunds(Exception):
    pass


def balance_of(account: Account) -> Decimal:
    total = account.entries.aggregate(total=Sum("amount"))["total"]
    return total or Decimal("0")


def ensure_user_account(user) -> Account:
    """Every user gets exactly one wallet account, provisioned on first money touch."""
    account, _ = Account.objects.get_or_create(
        kind=Account.Kind.USER, owner=user, code=f"user:{user.pk}", defaults={"currency": "TND"}
    )
    return account


def get_system_account(code: str) -> Account:
    """System accounts (revenue, clearing) may go negative: they absorb in-flight money."""
    account, _ = Account.objects.get_or_create(
        kind=Account.Kind.SYSTEM, code=code,
        defaults={"currency": "TND", "allow_negative": True},
    )
    return account


def transaction_exists(reference: str) -> bool:
    return Transaction.objects.filter(reference=reference).exists()


@transaction.atomic
def post_transaction(
    *, kind: str, reference: str, legs: list[tuple[Account, Decimal]], description: str = "",
    overdraft: frozenset = frozenset(),
):
    """
    Post a balanced set of ledger legs.
    legs: list of (account, amount). Amounts must sum to zero.
    overdraft: account pks allowed below zero for this posting only (a recorded debt).
    Idempotent: posting the same reference twice returns the existing transaction.
    """
    if sum(amount for _, amount in legs) != 0:
        raise ValueError("Ledger transaction does not balance")

    existing = Transaction.objects.filter(reference=reference).first()
    if existing:
        return existing

    # Lock accounts in a stable order to prevent deadlocks
    accounts = sorted({acc.pk: acc for acc, _ in legs}.values(), key=lambda a: a.pk)
    locked = {a.pk: a for a in Account.objects.select_for_update().filter(pk__in=[a.pk for a in accounts])}

    for account, amount in legs:
        acc = locked[account.pk]
        if amount < 0 and not acc.allow_negative and acc.pk not in overdraft and balance_of(acc) + amount < 0:
            raise InsufficientFunds(f"Account {acc.code} has insufficient funds")

    try:
        txn = Transaction.objects.create(kind=kind, reference=reference, description=description)
    except IntegrityError:
        return Transaction.objects.get(reference=reference)

    LedgerEntry.objects.bulk_create(
        [LedgerEntry(transaction=txn, account=locked[acc.pk], amount=amount) for acc, amount in legs]
    )
    publish_on_commit("wallet.posted", transaction_id=str(txn.pk))
    return txn


def transfer(*, source: Account, destination: Account, amount: Decimal, kind: str, reference: str, description=""):
    if amount <= 0:
        raise ValueError("Transfer amount must be positive")
    return post_transaction(
        kind=kind,
        reference=reference,
        legs=[(source, -amount), (destination, amount)],
        description=description,
    )


def legs_of(reference: str) -> list[tuple[Account, Decimal]]:
    return [(entry.account, entry.amount) for entry in LedgerEntry.objects.filter(
        transaction__reference=reference).select_related("account").order_by("pk")]


def reverse_transaction(*, original: str, reference: str, kind: str, description: str = "", overdraft=frozenset()):
    """Post the exact negation of an earlier posting. Returns None when the original never happened."""
    legs = legs_of(original)
    if not legs:
        return None
    return post_transaction(
        kind=kind, reference=reference, legs=[(account, -amount) for account, amount in legs],
        description=description, overdraft=overdraft,
    )
