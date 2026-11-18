from django.conf import settings
from django.contrib.gis.db import models
from django.db import models as dj_models

from core.models import TimeStampedModel


class Interest(TimeStampedModel):
    """A passenger's standing request for a future route. Matched against published rides."""

    passenger = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="interests")
    origin = models.PointField(geography=True, srid=4326)
    destination = models.PointField(geography=True, srid=4326)
    origin_label = models.CharField(max_length=150, blank=True, default="")
    destination_label = models.CharField(max_length=150, blank=True, default="")
    # The departure window the passenger is willing to travel in.
    earliest_departure = models.DateTimeField(db_index=True)
    latest_departure = models.DateTimeField()
    seats = models.PositiveSmallIntegerField(default=1)
    is_active = models.BooleanField(default=True, db_index=True)

    class Meta:
        constraints = [
            dj_models.CheckConstraint(
                condition=dj_models.Q(latest_departure__gte=dj_models.F("earliest_departure")),
                name="interest_window_positive",
            ),
            dj_models.CheckConstraint(condition=dj_models.Q(seats__gte=1), name="interest_seats_positive"),
        ]
        indexes = [models.Index(fields=["passenger", "is_active"])]
