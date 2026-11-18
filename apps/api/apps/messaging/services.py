from asgiref.sync import async_to_sync
from channels.layers import get_channel_layer
from django.db import transaction
from django.db.models import Q

from apps.bookings.state_machine import BookingStatus
from apps.rides.models import Ride
from apps.users.models import User

from .models import Conversation, Message

RETAINED_STATUSES = {
    BookingStatus.COMPLETED, BookingStatus.NO_SHOW,
    BookingStatus.CANCELLED_BY_PASSENGER, BookingStatus.CANCELLED_BY_DRIVER,
}
SENDABLE_RIDE_STATUSES = {Ride.Status.OPEN, Ride.Status.FULL, Ride.Status.IN_PROGRESS}


class NotConversationMember(PermissionError):
    """The account or booking is not currently eligible for this chat operation."""


@transaction.atomic
def get_or_create_conversation(booking) -> Conversation:
    return Conversation.objects.get_or_create(booking=booking)[0]


def _pk(value):
    return getattr(value, "pk", value)


def can_send(booking) -> bool:
    return booking.status == BookingStatus.CONFIRMED and booking.ride.status in SENDABLE_RIDE_STATUSES


def readable_conversations(user):
    """Read participant IDs, account activity and booking eligibility from the DB."""
    user_id = _pk(user)
    return Conversation.objects.select_related("booking", "booking__ride").filter(
        Q(booking__passenger_id=user_id, booking__passenger__is_active=True)
        | Q(booking__ride__driver_id=user_id, booking__ride__driver__is_active=True),
    ).filter(
        Q(booking__status=BookingStatus.CONFIRMED)
        | Q(booking__status__in=RETAINED_STATUSES, booking__paid_amount__isnull=False),
    )


def check_membership(*, conversation, user, for_send=False) -> Conversation:
    """Never authorize using a previously loaded conversation or user."""
    queryset = readable_conversations(user)
    if for_send:
        queryset = queryset.filter(
            booking__status=BookingStatus.CONFIRMED,
            booking__ride__status__in=SENDABLE_RIDE_STATUSES,
        )
    fresh = queryset.filter(pk=_pk(conversation)).first()
    if fresh is None:
        raise NotConversationMember("Chat is not available for this account or booking")
    return fresh


def message_frame(message) -> dict:
    from .serializers import MessageSerializer

    return {"type": "chat.message", "message": dict(MessageSerializer(message).data)}


def _broadcast_message(message) -> None:
    # Cancellation/ban may have committed since persistence. Recipients also
    # recheck their own fresh eligibility before delivering this group event.
    try:
        check_membership(conversation=message.conversation_id, user=message.sender_id, for_send=True)
    except NotConversationMember:
        return
    layer = get_channel_layer()
    if layer is not None:
        async_to_sync(layer.group_send)(f"chat_{message.conversation_id}", message_frame(message))


@transaction.atomic
def post_message(*, conversation, sender, body: str) -> Message:
    """Serialize sends with ride/booking cancellation; broadcast only on commit."""
    # Lazy import: booking creation itself calls this module.
    from apps.bookings.services import _lock

    conversation = Conversation.objects.get(pk=_pk(conversation))
    ride, booking = _lock(conversation.booking_id)  # ride first, then booking
    sender = User.objects.select_for_update().filter(pk=_pk(sender), is_active=True).first()
    if (
        sender is None
        or sender.pk not in {booking.passenger_id, ride.driver_id}
        or not can_send(booking)
    ):
        raise NotConversationMember("Chat is not available for this account or booking")
    if not isinstance(body, str) or not body.strip():
        raise ValueError("Message body must be a nonempty string")
    if len(body) > 2000:
        raise ValueError("Message body must not exceed 2000 characters")
    message = Message.objects.create(conversation=conversation, sender=sender, body=body.strip())
    transaction.on_commit(lambda: _broadcast_message(message))
    return message
