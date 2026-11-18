from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


class Vehicle(TimeStampedModel):
    driver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="vehicles")
    make = models.CharField(max_length=50)
    model = models.CharField(max_length=50)
    color = models.CharField(max_length=30, blank=True)
    plate_number = models.CharField(max_length=20, unique=True)  # e.g. "123 TU 4567"
    seats = models.PositiveSmallIntegerField()  # Passenger seats offered
    carte_grise = models.ForeignKey(
        "verification.VerificationDocument",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="vehicles",
        limit_choices_to={"doc_type": "CARTE_GRISE"},
    )
    is_active = models.BooleanField(default=True)

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(seats__gte=1), name="vehicle_seats_positive"),
        ]

    def __str__(self):
        return f"{self.make} {self.model} ({self.plate_number})"

    @property
    def is_verified(self) -> bool:
        """A vehicle is verified only through an approved Carte Grise. Derived, never stored."""
        return self.carte_grise is not None and self.carte_grise.status == "APPROVED"

    @property
    def is_bookable(self) -> bool:
        return self.is_active and self.is_verified
