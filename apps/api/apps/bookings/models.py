from django.conf import settings
from django.contrib.gis.db import models
from django.db import models as dj_models

from core.models import TimeStampedModel

from .state_machine import BookingStatus


class Booking(TimeStampedModel):
    ride = models.ForeignKey("rides.Ride", on_delete=models.PROTECT, related_name="bookings")
    passenger = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="bookings")
    seats = models.PositiveSmallIntegerField(default=1)
    pickup_point = models.PointField(geography=True, srid=4326)
    dropoff_point = models.PointField(geography=True, srid=4326)
    status = models.CharField(
        max_length=30, choices=BookingStatus.CHOICES, default=BookingStatus.REQUESTED, db_index=True
    )
    fare_amount = models.DecimalField(max_digits=10, decimal_places=3)
    # Recorded at settlement / refund so receipts never depend on today's campaigns or fee rate.
    subsidy_amount = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    voucher_amount = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    paid_amount = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)
    refunded_amount = models.DecimalField(max_digits=10, decimal_places=3, null=True, blank=True)

    class Meta:
        constraints = [
            dj_models.CheckConstraint(condition=dj_models.Q(seats__gte=1), name="booking_seats_positive"),
        ]


class BookingEvent(TimeStampedModel):
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="events")
    from_status = models.CharField(max_length=30)
    to_status = models.CharField(max_length=30)
    action = models.CharField(max_length=30)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
