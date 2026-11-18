from django.conf import settings
from django.db import models

from core.models import TimeStampedModel


class Conversation(TimeStampedModel):
    """A chat thread between the driver and the passenger of one booking."""

    booking = models.OneToOneField("bookings.Booking", on_delete=models.PROTECT, related_name="conversation")

    class Meta:
        ordering = ["created_at"]

    def __str__(self):
        return f"conversation:{self.booking_id}"


class Message(TimeStampedModel):
    conversation = models.ForeignKey(Conversation, on_delete=models.PROTECT, related_name="messages")
    sender = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="messages")
    body = models.TextField(max_length=2000)

    class Meta:
        ordering = ["created_at"]
        indexes = [models.Index(fields=["conversation", "created_at"])]
