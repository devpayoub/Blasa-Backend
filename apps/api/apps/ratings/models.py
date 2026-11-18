from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


class Rating(TimeStampedModel):
    """A one-way rating between the two participants of a COMPLETED booking."""

    booking = models.ForeignKey("bookings.Booking", on_delete=models.PROTECT, related_name="ratings")
    rater = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="ratings_given")
    ratee = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="ratings_received")
    score = models.PositiveSmallIntegerField()  # 1..5, enforced by a check constraint
    comment = models.CharField(max_length=500, blank=True, default="")

    class Meta:
        constraints = [
            models.CheckConstraint(condition=models.Q(score__gte=1) & models.Q(score__lte=5), name="rating_score_1_to_5"),
            models.UniqueConstraint(fields=["booking", "rater"], name="rating_one_per_booking_and_rater"),
        ]
        indexes = [models.Index(fields=["ratee", "created_at"])]
