import asyncio
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from datetime import timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

import pytest
from channels.db import database_sync_to_async
from channels.layers import get_channel_layer
from channels.testing import WebsocketCommunicator
from django.db import close_old_connections, transaction
from django.utils import timezone
from rest_framework.test import APIClient
from test_phase6_flow import _access_token, _make_booking, _make_driver

from apps.bookings.models import Booking
from apps.bookings.services import _lock
from apps.bookings.state_machine import BookingStatus
from apps.messaging import services
from apps.messaging.models import Conversation, Message
from apps.rides.models import Ride
from apps.users.models import User


@pytest.fixture
def chat():
    driver, vehicle = _make_driver("+21660100001")
    passenger = User.objects.create_user(phone_number="+21660100002", password="x")
    booking = _make_booking(driver, vehicle, passenger)
    Booking.objects.filter(pk=booking.pk).update(status=BookingStatus.CONFIRMED, paid_amount=Decimal("15.000"))
    conversation = Conversation.objects.select_related("booking", "booking__ride").get(booking=booking)
    return driver, passenger, booking, conversation


def _client(user):
    client = APIClient()
    client.force_authenticate(user)
    return client


def _socket(conversation, user):
    from config.asgi import application

    url = f"/ws/chat/{conversation.pk}/?{urlencode({'token': _access_token(user)})}"
    return WebsocketCommunicator(application, url)


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("status", [BookingStatus.REQUESTED, BookingStatus.ACCEPTED, BookingStatus.REJECTED])
def test_unconfirmed_chat_refuses_http_and_socket(chat, status):
    driver, passenger, booking, conversation = chat
    # Even a non-null amount cannot unlock requested/accepted/rejected threads.
    Booking.objects.filter(pk=booking.pk).update(status=status)
    communicators = []
    for user in (passenger, driver):
        client = _client(user)
        assert client.get("/api/v1/conversations/").data["results"] == []
        assert client.get(f"/api/v1/conversations/{conversation.pk}/").status_code == 404
        assert client.get(f"/api/v1/conversations/{conversation.pk}/messages/").status_code == 403
        assert client.post(
            f"/api/v1/conversations/{conversation.pk}/messages/", {"body": "hello"}, format="json"
        ).status_code == 403
        communicators.append(_socket(conversation, user))

    async def scenario():
        for communicator in communicators:
            assert await communicator.connect() == (False, 4403)
            await communicator.disconnect()

    asyncio.run(scenario())
    assert not Message.objects.exists()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("status", sorted(services.RETAINED_STATUSES))
@pytest.mark.parametrize("paid", [None, Decimal("0.000"), Decimal("15.000")])
def test_terminal_history_requires_paid_amount_and_never_sends(chat, status, paid):
    driver, passenger, booking, conversation = chat
    Message.objects.create(conversation=conversation, sender=passenger, body="retained")
    Booking.objects.filter(pk=booking.pk).update(status=status, paid_amount=paid)
    for user in (passenger, driver):
        client = _client(user)
        rows = client.get("/api/v1/conversations/").data["results"]
        detail = client.get(f"/api/v1/conversations/{conversation.pk}/")
        history = client.get(f"/api/v1/conversations/{conversation.pk}/messages/")
        if paid is None:
            assert rows == []
            assert detail.status_code == 404
            assert history.status_code == 403
        else:
            assert [row["id"] for row in rows] == [str(conversation.pk)]
            assert detail.status_code == 200
            assert detail.data["booking_status"] == status
            assert detail.data["can_send"] is False
            assert history.status_code == 200
            assert [row["body"] for row in history.data] == ["retained"]
        assert client.post(
            f"/api/v1/conversations/{conversation.pk}/messages/", {"body": "too late"}, format="json"
        ).status_code == 403

    communicator = _socket(conversation, passenger)

    async def scenario():
        assert await communicator.connect() == (False, 4403)
        await communicator.disconnect()

    asyncio.run(scenario())
    assert Message.objects.count() == 1


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("participant", [0, 1])
def test_inactive_account_cannot_read_send_or_connect_with_stale_user(chat, participant):
    user = chat[participant]
    conversation = chat[3]
    communicator = _socket(conversation, user)
    User.objects.filter(pk=user.pk).update(is_active=False)
    assert user.is_active  # deliberately stale API authentication and service input
    client = _client(user)
    assert client.get("/api/v1/conversations/").data["results"] == []
    assert client.get(f"/api/v1/conversations/{conversation.pk}/").status_code == 404
    assert client.get(f"/api/v1/conversations/{conversation.pk}/messages/").status_code == 403
    assert client.post(
        f"/api/v1/conversations/{conversation.pk}/messages/", {"body": "hello"}, format="json"
    ).status_code == 403
    with pytest.raises(services.NotConversationMember):
        services.post_message(conversation=conversation, sender=user, body="hello")

    async def scenario():
        assert await communicator.connect() == (False, 4403)
        await communicator.disconnect()

    asyncio.run(scenario())


@pytest.mark.django_db
def test_stale_conversation_cancellation_is_read_only(chat):
    _, passenger, booking, conversation = chat
    Booking.objects.filter(pk=booking.pk).update(status=BookingStatus.CANCELLED_BY_DRIVER)
    assert conversation.booking.status == BookingStatus.CONFIRMED
    fresh = services.check_membership(conversation=conversation, user=passenger)
    assert fresh.booking.status == BookingStatus.CANCELLED_BY_DRIVER
    with pytest.raises(services.NotConversationMember):
        services.post_message(conversation=conversation, sender=passenger, body="stale send")
    assert not Message.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("participant", [0, 1])
def test_permission_reads_fresh_participant_ids(chat, participant):
    driver, passenger, booking, conversation = chat
    old_user = (driver, passenger)[participant]
    new_user = User.objects.create_user(phone_number="+21660100003", password="x")
    if participant == 0:
        Ride.objects.filter(pk=booking.ride_id).update(driver=new_user)
    else:
        Booking.objects.filter(pk=booking.pk).update(passenger=new_user)
    with pytest.raises(services.NotConversationMember):
        services.check_membership(conversation=conversation, user=old_user)
    with pytest.raises(services.NotConversationMember):
        services.post_message(conversation=conversation, sender=old_user, body="stale member")
    message = services.post_message(conversation=conversation, sender=new_user, body="new member")
    assert message.sender_id == new_user.pk
    detail = _client(new_user).get(f"/api/v1/conversations/{conversation.pk}/")
    assert detail.data["booking_status"] == BookingStatus.CONFIRMED
    assert detail.data["can_send"] is True
    assert str(new_user.pk) in detail.data["participants"]
    assert str(old_user.pk) not in detail.data["participants"]


@pytest.mark.django_db
@pytest.mark.parametrize("body", [None, 1, True, [], {}, "", "   ", "a" * 2001])
def test_message_body_validation(chat, body):
    _, passenger, _, conversation = chat
    assert _client(passenger).post(
        f"/api/v1/conversations/{conversation.pk}/messages/", {"body": body}, format="json"
    ).status_code == 400
    with pytest.raises(ValueError):
        services.post_message(conversation=conversation, sender=passenger, body=body)
    assert not Message.objects.exists()


@pytest.mark.django_db
def test_http_broadcast_runs_only_after_commit_with_normalized_frame(chat, django_capture_on_commit_callbacks):
    _, passenger, _, conversation = chat
    with patch.object(get_channel_layer(), "group_send", new_callable=AsyncMock) as broadcast:
        with django_capture_on_commit_callbacks(execute=True):
            response = _client(passenger).post(
                f"/api/v1/conversations/{conversation.pk}/messages/", {"body": "a" * 2000}, format="json"
            )
            assert response.status_code == 201
            broadcast.assert_not_called()
        broadcast.assert_awaited_once_with(
            f"chat_{conversation.pk}", {"type": "chat.message", "message": dict(response.data)}
        )


@pytest.mark.django_db
def test_rollback_does_not_persist_or_broadcast(chat):
    _, passenger, _, conversation = chat
    with patch("apps.messaging.services._broadcast_message") as broadcast:
        with pytest.raises(RuntimeError), transaction.atomic():
            services.post_message(conversation=conversation, sender=passenger, body="rolled back")
            raise RuntimeError("rollback")
        broadcast.assert_not_called()
    assert not Message.objects.exists()


@pytest.mark.django_db
@pytest.mark.parametrize("revoke", ["ban", "cancel"])
def test_broadcast_rechecks_sender_and_booking_after_commit(chat, django_capture_on_commit_callbacks, revoke):
    _, passenger, booking, conversation = chat
    with patch.object(get_channel_layer(), "group_send", new_callable=AsyncMock) as broadcast:
        with django_capture_on_commit_callbacks(execute=True):
            services.post_message(conversation=conversation, sender=passenger, body="committed earlier")
            if revoke == "ban":
                User.objects.filter(pk=passenger.pk).update(is_active=False)
            else:
                Booking.objects.filter(pk=booking.pk).update(status=BookingStatus.CANCELLED_BY_DRIVER)
        broadcast.assert_not_called()


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("revoke", ["ban", "cancel", "complete", "participant"])
@pytest.mark.parametrize("activity", ["send", "broadcast"])
def test_already_open_socket_closes_before_activity_after_revocation(chat, revoke, activity):
    _, passenger, booking, conversation = chat
    communicator = _socket(conversation, passenger)
    message = Message.objects.create(conversation=conversation, sender=passenger, body="queued before revocation")
    frame = services.message_frame(message)

    @database_sync_to_async
    def change_eligibility():
        if revoke == "ban":
            User.objects.filter(pk=passenger.pk).update(is_active=False)
        elif revoke == "participant":
            new_user = User.objects.create_user(phone_number="+21660100003", password="x")
            Booking.objects.filter(pk=booking.pk).update(passenger=new_user)
        else:
            status = BookingStatus.COMPLETED if revoke == "complete" else BookingStatus.CANCELLED_BY_DRIVER
            Booking.objects.filter(pk=booking.pk).update(status=status)

    async def scenario():
        assert (await communicator.connect())[0]
        await change_eligibility()
        if activity == "send":
            await communicator.send_json_to({"body": "must not persist"})
        else:
            await get_channel_layer().group_send(f"chat_{conversation.pk}", frame)
        assert await communicator.receive_output() == {"type": "websocket.close", "code": 4403}
        await communicator.disconnect()

    asyncio.run(scenario())
    assert Message.objects.count() == 1


@pytest.mark.django_db(transaction=True)
def test_http_send_reaches_active_socket_but_never_banned_recipient(chat):
    driver, passenger, _, conversation = chat
    passenger_comm = _socket(conversation, passenger)
    driver_comm = _socket(conversation, driver)

    @database_sync_to_async
    def ban_and_send():
        User.objects.filter(pk=passenger.pk).update(is_active=False)
        response = _client(driver).post(
            f"/api/v1/conversations/{conversation.pk}/messages/", {"body": "driver message"}, format="json"
        )
        assert response.status_code == 201
        return dict(response.data)

    async def scenario():
        assert (await passenger_comm.connect())[0]
        assert (await driver_comm.connect())[0]
        payload = await ban_and_send()
        assert await driver_comm.receive_json_from() == {"type": "chat.message", "message": payload}
        assert await passenger_comm.receive_output() == {"type": "websocket.close", "code": 4403}
        await passenger_comm.disconnect()
        await driver_comm.disconnect()

    asyncio.run(scenario())


@pytest.mark.django_db(transaction=True)
def test_send_waits_for_ride_first_cancellation_and_then_refuses(chat):
    _, passenger, booking, conversation = chat

    def send():
        close_old_connections()
        try:
            services.post_message(conversation=conversation, sender=passenger, body="racing cancellation")
        finally:
            close_old_connections()

    with ThreadPoolExecutor(max_workers=1) as pool:
        with transaction.atomic():
            # Hold just the ride first: sending must not grab the booking ahead
            # of cancellation, or the booking update below would deadlock.
            Ride.objects.select_for_update().get(pk=booking.ride_id)
            future = pool.submit(send)
            with pytest.raises(TimeoutError):
                future.result(timeout=0.2)
            _, locked_booking = _lock(booking.pk)
            locked_booking.status = BookingStatus.CANCELLED_BY_DRIVER
            locked_booking.save(update_fields=["status"])
        with pytest.raises(services.NotConversationMember):
            future.result(timeout=5)
    assert not Message.objects.exists()


@pytest.mark.django_db(transaction=True)
def test_open_socket_rejects_activity_after_access_token_expiry(chat):
    _, passenger, _, conversation = chat
    communicator = _socket(conversation, passenger)
    future = timezone.now() + timedelta(days=1)

    async def scenario():
        assert (await communicator.connect())[0]
        with patch("apps.messaging.consumers.timezone.now", return_value=future):
            await communicator.send_json_to({"body": "expired access"})
            assert await communicator.receive_output() == {"type": "websocket.close", "code": 4401}
        await communicator.disconnect()

    asyncio.run(scenario())
    assert not Message.objects.exists()
