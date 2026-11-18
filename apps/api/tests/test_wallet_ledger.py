from decimal import Decimal

import pytest

from apps.wallet.models import Account, LedgerEntry
from apps.wallet.services import InsufficientFunds, post_transaction, transfer


@pytest.mark.django_db
def test_unbalanced_transaction_is_rejected():
    a = Account.objects.create(kind="SYSTEM", code="sys:a")
    with pytest.raises(ValueError):
        post_transaction(kind="TEST", reference="t1", legs=[(a, Decimal("10"))])


@pytest.mark.django_db
def test_transfer_respects_balance():
    funder = Account.objects.create(kind="SYSTEM", code="sys:funder", allow_negative=True)
    user = Account.objects.create(kind="USER", code="user:1")
    sink = Account.objects.create(kind="SYSTEM", code="sys:sink", allow_negative=True)

    transfer(source=funder, destination=user, amount=Decimal("20"), kind="TOPUP", reference="topup-1")
    transfer(source=user, destination=sink, amount=Decimal("5"), kind="RIDE_PAYMENT", reference="ride-1")

    with pytest.raises(InsufficientFunds):
        transfer(source=user, destination=sink, amount=Decimal("100"), kind="RIDE_PAYMENT", reference="ride-2")


@pytest.mark.django_db
def test_posting_same_reference_is_idempotent():
    funder = Account.objects.create(kind="SYSTEM", code="sys:f2", allow_negative=True)
    user = Account.objects.create(kind="USER", code="user:2")
    first = transfer(source=funder, destination=user, amount=Decimal("10"), kind="TOPUP", reference="dup-1")
    second = transfer(source=funder, destination=user, amount=Decimal("10"), kind="TOPUP", reference="dup-1")
    assert first.pk == second.pk


@pytest.mark.django_db
def test_ledger_entries_are_immutable():
    funder = Account.objects.create(kind="SYSTEM", code="sys:f3", allow_negative=True)
    user = Account.objects.create(kind="USER", code="user:3")
    txn = transfer(source=funder, destination=user, amount=Decimal("10"), kind="TOPUP", reference="dup-2")

    entry = txn.entries.first()
    with pytest.raises(PermissionError):
        entry.save()

    with pytest.raises(PermissionError):
        entry.delete()

    with pytest.raises(PermissionError):
        LedgerEntry.objects.update(amount=Decimal("0"))
