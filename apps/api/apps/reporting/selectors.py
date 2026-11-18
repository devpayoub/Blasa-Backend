"""Admin-only reporting selectors. Read-only aggregates over bookings, rides,
and the ledger (revenue, sponsor spend, refunds)."""

import csv
import io
from decimal import Decimal

from django.db.models import Avg, Count, Sum
from django.utils import timezone

from apps.bookings.models import Booking, BookingEvent
from apps.bookings.state_machine import BookingStatus
from apps.rides.models import Ride
from apps.wallet.models import LedgerEntry, Transaction
from apps.wallet.services import get_system_account

REVENUE_ACCOUNT = "sys:platform_revenue"
MILLI = Decimal("0.001")


def rides_summary() -> dict:
    by_status = dict(Ride.objects.values_list("status").annotate(total=Count("id")))
    totals = Ride.objects.aggregate(total=Count("id"), avg_seats=Avg("seats_total"))
    return {
        "total_rides": totals["total"] or 0,
        "avg_seats_offered": round(totals["avg_seats"], 2) if totals["avg_seats"] else 0,
        "by_status": {status: count for status, count in by_status.items()},
    }


def revenue_summary(days: int = 30) -> dict:
    """Platform revenue, sponsor subsidies, and refunds over the last `days`."""
    since = timezone.now() - timezone.timedelta(days=days)
    revenue_account = get_system_account(REVENUE_ACCOUNT)

    by_kind = {}
    for kind in Transaction.objects.values_list("kind", flat=True).distinct():
        entries = LedgerEntry.objects.filter(
            account=revenue_account, transaction__kind=kind, created_at__gte=since
        )
        total = entries.aggregate(total=Sum("amount"))["total"]
        if total is not None:
            by_kind[kind] = total

    sponsor_spent = (
        LedgerEntry.objects.filter(
            transaction__kind="SPONSOR_SUBSIDY", amount__lt=0, created_at__gte=since
        ).aggregate(total=Sum("amount"))["total"]
        or Decimal("0")
    )
    refunds = Transaction.objects.filter(kind="REFUND", created_at__gte=since).count()

    return {
        "window_days": days,
        "platform_revenue": by_kind.get("RIDE_PAYMENT", Decimal("0")),
        "sponsor_subsidies": abs(sponsor_spent),
        "refunds": refunds,
        "revenue_by_kind": by_kind,
    }


def cancellation_summary() -> dict:
    total = Booking.objects.count()
    cancelled_by_passenger = BookingEvent.objects.filter(action="cancel_passenger").count()
    cancelled_by_driver = BookingEvent.objects.filter(action="cancel_driver").count()
    completed = Booking.objects.filter(status=BookingStatus.COMPLETED).count()
    rejected = BookingEvent.objects.filter(action="reject").count()
    rate = (cancelled_by_driver + cancelled_by_passenger) / total if total else 0
    return {
        "total_bookings": total,
        "completed": completed,
        "cancelled_by_passenger": cancelled_by_passenger,
        "cancelled_by_driver": cancelled_by_driver,
        "rejected": rejected,
        "cancellation_rate": round(rate, 4),
    }


def export_csv(dataset: str) -> (str, str):
    """Return (filename, csv_text) for the requested dataset."""
    buffer = io.StringIO()
    writer = csv.writer(buffer)

    if dataset == "rides":
        writer.writerow(["id", "driver", "status", "seats_total", "price_per_seat", "departure_at", "created_at"])
        for ride in Ride.objects.select_related("driver").all():
            writer.writerow([
                ride.id, ride.driver.phone_number, ride.status, ride.seats_total,
                ride.price_per_seat, ride.departure_at.isoformat(), ride.created_at.isoformat(),
            ])
        return "rides.csv", buffer.getvalue()

    if dataset == "bookings":
        writer.writerow([
            "id", "passenger", "ride", "status", "seats", "fare_amount", "created_at",
        ])
        for booking in Booking.objects.select_related("passenger", "ride").all():
            writer.writerow([
                booking.id, booking.passenger.phone_number, booking.ride_id, booking.status,
                booking.seats, booking.fare_amount, booking.created_at.isoformat(),
            ])
        return "bookings.csv", buffer.getvalue()

    if dataset == "ledger":
        writer.writerow(["reference", "kind", "account", "amount", "created_at"])
        for entry in LedgerEntry.objects.select_related("transaction", "account").all():
            writer.writerow([
                entry.transaction.reference, entry.transaction.kind, entry.account.code,
                entry.amount, entry.created_at.isoformat(),
            ])
        return "ledger.csv", buffer.getvalue()

    raise ValueError(f"Unknown dataset {dataset!r}")
