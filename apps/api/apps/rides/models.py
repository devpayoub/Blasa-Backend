from django.conf import settings
from django.contrib.gis.db import models
from django.db import models as dj_models

from core.models import TimeStampedModel


class Ride(TimeStampedModel):
    class Status(dj_models.TextChoices):
        OPEN = "OPEN", "Open for booking"
        FULL = "FULL", "Fully booked"
        IN_PROGRESS = "IN_PROGRESS", "In progress"
        COMPLETED = "COMPLETED", "Completed"
        CANCELLED = "CANCELLED", "Cancelled"

    class Atmosphere(dj_models.TextChoices):
        MUSIC = "MUSIC", "Music on"
        CONVERSATION = "CONVERSATION", "Happy to chat"
        QUIET = "QUIET", "Quiet ride"

    driver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="rides")
    vehicle = models.ForeignKey(
        "vehicles.Vehicle",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="rides",
    )
    origin = models.PointField(geography=True, srid=4326)
    destination = models.PointField(geography=True, srid=4326)
    origin_label = models.CharField(max_length=150)  # e.g. "Tunis, Lac 2"
    destination_label = models.CharField(max_length=150)
    departure_at = models.DateTimeField(db_index=True)
    seats_total = models.PositiveSmallIntegerField()
    seats_available = models.PositiveSmallIntegerField()
    price_per_seat = models.DecimalField(max_digits=10, decimal_places=3)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN, db_index=True)
    notes = models.CharField(max_length=500, blank=True, default="")
    atmosphere = models.CharField(max_length=20, choices=Atmosphere.choices, blank=True, default="")
    preferred_interests = models.ManyToManyField("users.InterestTag", blank=True, related_name="+")
    started_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    cancelled_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            dj_models.CheckConstraint(
                condition=dj_models.Q(seats_available__gte=0)
                & dj_models.Q(seats_available__lte=dj_models.F("seats_total")),
                name="ride_seats_within_capacity",
            ),
            dj_models.CheckConstraint(condition=dj_models.Q(seats_total__gte=1), name="ride_seats_positive"),
        ]
        indexes = [models.Index(fields=["status", "departure_at"])]


# Importable alias for SPECTACULAR_SETTINGS.ENUM_NAME_OVERRIDES (stable schema names).
RIDE_STATUS_CHOICES = Ride.Status.choices
