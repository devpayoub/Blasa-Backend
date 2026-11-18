from decimal import Decimal

import pytest
from django.db import connection
from django.db.migrations.executor import MigrationExecutor

from apps.users.models import User
from apps.wallet.models import Account, LedgerEntry, Transaction

pytestmark = pytest.mark.django_db(transaction=True)
OLD = ("users", "0001_initial")
NEW = ("users", "0002_identity_contacts")


def _migrate(target):
    executor = MigrationExecutor(connection)
    executor.migrate([target])
    return executor.loader.project_state([target]).apps.get_model("users", "User")


def _migrate_latest():
    # Rolling users back also unapplies later migrations that depend on it; restore every app to its leaf.
    executor = MigrationExecutor(connection)
    executor.migrate(executor.loader.graph.leaf_nodes())


def test_identity_data_migration_normalizes_without_merging_or_fabricating_proof():
    historical = _migrate(OLD)
    try:
        first = historical.objects.create(phone_number="+21698004001", email="  MiXeD@Example.COM  ")
        second = historical.objects.create(phone_number="+21698004002", email="")
        wallet = Account.objects.create(kind="USER", owner_id=first.pk, code=f"user:{first.pk}")
        funder = Account.objects.create(kind="SYSTEM", code="migration:funder", allow_negative=True)
        posting = Transaction.objects.create(kind="TOPUP", reference="migration:synthetic")
        LedgerEntry.objects.bulk_create([
            LedgerEntry(transaction=posting, account=funder, amount=Decimal("-12")),
            LedgerEntry(transaction=posting, account=wallet, amount=Decimal("12")),
        ])
        before_entries = list(LedgerEntry.objects.order_by("id").values())
        _migrate(NEW)
        first_live = User.objects.get(pk=first.pk)
        second_live = User.objects.get(pk=second.pk)
        assert first_live.email == "mixed@example.com" and first_live.phone_number == first.phone_number
        assert second_live.phone_number == second.phone_number and second_live.email == ""
        assert first_live.phone_verified_at is None and first_live.email_verified_at is None
        wallet.refresh_from_db()
        assert wallet.owner_id == first.pk
        assert list(LedgerEntry.objects.order_by("id").values()) == before_entries
        assert User.objects.count() == 2
    finally:
        _migrate_latest()


@pytest.mark.parametrize("failure", ["duplicate", "no_contact"])
def test_identity_data_migration_aborts_with_only_ids_and_preserves_all_records(failure):
    historical = _migrate(OLD)
    first = historical.objects.create(phone_number="+21698004003", email="  Conflict@Example.COM ")
    second = historical.objects.create(phone_number="+21698004004", email="conflict@example.com")
    if failure == "no_contact":
        historical.objects.filter(pk=second.pk).update(phone_number="", email=" ")
    wallet = Account.objects.create(kind="USER", owner_id=first.pk, code=f"user:{first.pk}")
    before = list(historical.objects.order_by("id").values("id", "phone_number", "email"))
    try:
        with pytest.raises(RuntimeError) as error:
            _migrate(NEW)
        message = str(error.value)
        assert str(second.pk) in message
        if failure == "duplicate":
            assert str(first.pk) in message
        assert "example" not in message.lower() and "+216" not in message
        assert list(historical.objects.order_by("id").values("id", "phone_number", "email")) == before
        wallet.refresh_from_db()
        assert wallet.owner_id == first.pk
        assert historical.objects.count() == 2
    finally:
        # Repair only the synthetic fixture so the test database returns to latest.
        historical.objects.filter(pk=second.pk).update(phone_number="+21698004004", email="different@example.com")
        _migrate_latest()
