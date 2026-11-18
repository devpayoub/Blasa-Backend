from datetime import timedelta
from decimal import Decimal
from urllib.parse import urlencode

import pytest
from channels.testing import WebsocketCommunicator
from django.contrib.gis.geos import Point
from django.utils import timezone
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from apps.bookings.models import Booking
from apps.messaging.models import Conversation, Message
from apps.notifications.models import DeviceToken, Notification
from apps.payments import services as payment_services
from apps.payments.models import PaymentIntent
from apps.rides import services as ride_services
from apps.users.models import Profile, User
from apps.vehicles.models import Vehicle
from apps.verification.models import VerificationDocument

TUNIS = Point(10.18, 36.80, srid=4326)
SOUSSE = Point(10.64, 35.83, srid=4326)


def _make_driver(phone: str):
    driver = User.objects.create_user(phone_number=phone, password="x", role=User.Role.DRIVER)
    document = VerificationDocument.objects.create(
        user=driver, doc_type=VerificationDocument.DocType.CARTE_GRISE, storage_key=f"verification/{phone}.pdf",
        status=VerificationDocument.Status.APPROVED,
    )
    vehicle = Vehicle.objects.create(
        driver=driver, make="Dacia", model="Logan", plate_number=f"{phone[-4:]} TU 4444",
        seats=4, carte_grise=document,
    )
    return driver, vehicle


def _make_ride(driver, vehicle, seats_total=2, departure_at=None):
    return ride_services.publish_ride(
        driver=driver, vehicle=vehicle,
        origin=TUNIS, destination=SOUSSE,
        origin_label="Tunis", destination_label="Sousse",
        departure_at=departure_at or timezone.now() + timedelta(days=1),
        seats_total=seats_total, price_per_seat=Decimal("15.000"),
    )


def _make_booking(driver, vehicle, passenger, seats=1):
    ride = _make_ride(driver, vehicle)
    client = APIClient()
    client.force_authenticate(passenger)
    response = client.post(
        "/api/v1/bookings/",
        {"ride": str(ride.id), "seats": seats,
         "pickup_point": {"type": "Point", "coordinates": [10.20, 36.79]},
         "dropoff_point": {"type": "Point", "coordinates": [10.60, 35.85]}},
        format="json",
    )
    assert response.status_code == 201
    return Booking.objects.get(pk=response.data["id"])


def _transition(client, booking, action):
    return client.post(f"/api/v1/bookings/{booking.id}/transitions/{action}/")


def _pay(client, booking):
    """Passenger approves the server quote, then confirms with exactly that amount."""
    quote = client.get(f"/api/v1/bookings/{booking.id}/payment-quote/")
    assert quote.status_code == 200, quote.data
    return client.post(
        f"/api/v1/bookings/{booking.id}/transitions/confirm/",
        {"expected_out_of_pocket": str(quote.data["out_of_pocket"])}, format="json",
    )


def _start(client, booking):
    return client.post(f"/api/v1/rides/{booking.ride_id}/start/")


def _access_token(user) -> str:
    return str(RefreshToken.for_user(user).access_token)


# ---------------------------------------------------------------- messaging


@pytest.mark.django_db
def test_every_booking_gets_a_chat_thread_scoped_to_participants():
    driver, vehicle = _make_driver("+21660000001")
    passenger = User.objects.create_user(phone_number="+21660000002", password="x")
    booking = _make_booking(driver, vehicle, passenger)

    # One conversation exists, tied to the booking; chat opens on confirmation.
    assert Conversation.objects.count() == 1
    conversation = Conversation.objects.get(booking=booking)
    Booking.objects.filter(pk=booking.pk).update(status="CONFIRMED", paid_amount=Decimal("15.000"))

    # Both participants see it; a stranger does not.
    passenger_client = APIClient()
    passenger_client.force_authenticate(passenger)
    driver_client = APIClient()
    driver_client.force_authenticate(driver)
    stranger_client = APIClient()
    stranger_client.force_authenticate(User.objects.create_user(phone_number="+21660000003", password="x"))

    assert str(conversation.id) in [c["id"] for c in passenger_client.get("/api/v1/conversations/").data["results"]]
    assert str(conversation.id) in [c["id"] for c in driver_client.get("/api/v1/conversations/").data["results"]]
    assert stranger_client.get(f"/api/v1/conversations/{conversation.id}/").status_code == 404


@pytest.mark.django_db
def test_message_rest_send_and_history_membership():
    driver, vehicle = _make_driver("+21660000004")
    passenger = User.objects.create_user(phone_number="+21660000005", password="x")
    booking = _make_booking(driver, vehicle, passenger)
    Booking.objects.filter(pk=booking.pk).update(status="CONFIRMED", paid_amount=Decimal("15.000"))
    conversation = Conversation.objects.get(booking=booking)
    stranger_client = APIClient()
    stranger_client.force_authenticate(User.objects.create_user(phone_number="+21660000006", password="x"))

    passenger_client = APIClient()
    passenger_client.force_authenticate(passenger)
    sent = passenger_client.post(
        f"/api/v1/conversations/{conversation.id}/messages/", {"body": "Salam, je serai là à l'heure."},
        format="json",
    )
    assert sent.status_code == 201

    # History is member-scoped and persisted in order.
    driver_client = APIClient()
    driver_client.force_authenticate(driver)
    history = driver_client.get(f"/api/v1/conversations/{conversation.id}/messages/")
    assert history.status_code == 200
    assert [m["body"] for m in history.data] == ["Salam, je serai là à l'heure."]

    # Empty messages are refused; strangers get 403.
    assert passenger_client.post(
        f"/api/v1/conversations/{conversation.id}/messages/", {"body": "   "}, format="json"
    ).status_code == 400
    assert stranger_client.get(f"/api/v1/conversations/{conversation.id}/messages/").status_code == 403
    assert stranger_client.post(
        f"/api/v1/conversations/{conversation.id}/messages/", {"body": "hi"}, format="json"
    ).status_code == 403


@pytest.mark.django_db(transaction=True)
def test_chat_consumer_membership_persistence_and_broadcast():
    import asyncio

    from config.asgi import application

    driver, vehicle = _make_driver("+21660000007")
    passenger = User.objects.create_user(phone_number="+21660000008", password="x")
    booking = _make_booking(driver, vehicle, passenger)
    Booking.objects.filter(pk=booking.pk).update(status="CONFIRMED", paid_amount=Decimal("15.000"))
    conversation = Conversation.objects.get(booking=booking)
    stranger = User.objects.create_user(phone_number="+21660000009", password="x")

    stranger_url = f"/ws/chat/{conversation.id}/?{urlencode({'token': _access_token(stranger)})}"
    passenger_url = f"/ws/chat/{conversation.id}/?{urlencode({'token': _access_token(passenger)})}"
    garbage_url = f"/ws/chat/{conversation.id}/?token=garbage"

    async def scenario():
        # A stranger is refused on connect (authenticated, but not a participant).
        stranger_comm = WebsocketCommunicator(application, stranger_url)
        connected, _ = await stranger_comm.connect()
        assert not connected
        await stranger_comm.disconnect()

        # The passenger connects with a JWT, sends, and receives their own broadcast.
        passenger_comm = WebsocketCommunicator(application, passenger_url)
        connected, _ = await passenger_comm.connect()
        assert connected
        await passenger_comm.send_json_to({"body": "Salam khoya"})
        reply = await passenger_comm.receive_json_from()
        assert reply["type"] == "chat.message"
        assert reply["message"]["body"] == "Salam khoya"
        assert reply["message"]["conversation"] == str(conversation.id)
        assert reply["message"]["sender"] == str(passenger.id)
        await passenger_comm.disconnect()

        # Garbage tokens are bounced at connect.
        anon_comm = WebsocketCommunicator(application, garbage_url)
        connected, _ = await anon_comm.connect()
        assert not connected
        await anon_comm.disconnect()

    asyncio.run(scenario())

    # The message was persisted BEFORE the broadcast and survives the socket.
    assert Message.objects.filter(conversation=conversation, body="Salam khoya").exists()


# ---------------------------------------------------------------- notifications


@pytest.mark.django_db
def test_booking_transitions_create_localized_notifications(django_capture_on_commit_callbacks):
    driver, vehicle = _make_driver("+21660000010")
    passenger = User.objects.create_user(phone_number="+21660000011", password="x", preferred_language="en")
    with django_capture_on_commit_callbacks(execute=True):
        booking = _make_booking(driver, vehicle, passenger)
    # The driver hears about the new request.
    assert Notification.objects.filter(user=driver, kind="booking.created").count() == 1

    driver_client = APIClient()
    driver_client.force_authenticate(driver)
    with django_capture_on_commit_callbacks(execute=True):
        assert _transition(driver_client, booking, "accept").status_code == 200

    notes = Notification.objects.filter(user=passenger, kind="booking.status_changed")
    assert notes.count() == 1
    assert notes.first().title == "Booking accepted"  # passenger's language
    assert notes.first().payload["booking_id"] == str(booking.id)

    # The driver gets their own (French default) copy.
    driver_note = Notification.objects.filter(user=driver, kind="booking.status_changed").first()
    assert driver_note.title == "Réservation acceptée"


@pytest.mark.django_db
def test_inbox_scoping_and_mark_read(django_capture_on_commit_callbacks):
    driver, vehicle = _make_driver("+21660000012")
    passenger = User.objects.create_user(phone_number="+21660000013", password="x")
    booking = _make_booking(driver, vehicle, passenger)
    driver_client = APIClient()
    driver_client.force_authenticate(driver)
    with django_capture_on_commit_callbacks(execute=True):
        _transition(driver_client, booking, "accept")

    passenger_client = APIClient()
    passenger_client.force_authenticate(passenger)
    inbox = passenger_client.get("/api/v1/notifications/")
    assert inbox.status_code == 200
    note_id = inbox.data["results"][0]["id"]
    assert inbox.data["results"][0]["read_at"] is None

    read = passenger_client.post(f"/api/v1/notifications/{note_id}/read/")
    assert read.status_code == 200
    assert read.data["read_at"] is not None

    # Strangers cannot mark someone else's notification read.
    stranger = APIClient()
    stranger.force_authenticate(User.objects.create_user(phone_number="+21660000014", password="x"))
    assert stranger.post(f"/api/v1/notifications/{note_id}/read/").status_code == 404


@pytest.mark.django_db
def test_device_token_registration_and_match_notification():
    client = APIClient()
    client.force_authenticate(User.objects.create_user(phone_number="+21660000015", password="x"))
    registered = client.post(
        "/api/v1/notifications/devices/", {"token": "fcm-token-1", "platform": "IOS"}, format="json"
    )
    assert registered.status_code == 201
    assert DeviceToken.objects.filter(token="fcm-token-1", platform="IOS").exists()

    # Re-registering is idempotent; deleting removes it.
    assert client.post(
        "/api/v1/notifications/devices/", {"token": "fcm-token-1", "platform": "IOS"}, format="json"
    ).status_code == 201
    assert client.delete("/api/v1/notifications/devices/", {"token": "fcm-token-1"}, format="json").status_code == 204
    assert not DeviceToken.objects.filter(token="fcm-token-1").exists()

    # match.found lands in the passenger's inbox.
    passenger = User.objects.get(phone_number="+21660000015")
    from core.events import publish

    publish(
        "match.found", ride_id="00000000-0000-0000-0000-000000000001",
        interest_id="00000000-0000-0000-0000-000000000002", passenger_id=passenger.id,
        driver_id=passenger.id, score=87.5,
    )
    assert Notification.objects.filter(user=passenger, kind="match.found").exists()


# ---------------------------------------------------------------- ratings


@pytest.mark.django_db
def test_two_way_rating_only_after_completion_and_average_update():
    driver, vehicle = _make_driver("+21660000016")
    passenger = User.objects.create_user(phone_number="+21660000017", password="x")
    booking = _make_booking(driver, vehicle, passenger)
    passenger_client = APIClient()
    passenger_client.force_authenticate(passenger)
    driver_client = APIClient()
    driver_client.force_authenticate(driver)

    # Cannot rate before completion (wallet is bypassed here by topping up first).
    payment_services.create_topup(user=passenger, amount=Decimal("50.000"), gateway=PaymentIntent.Gateway.CASH)
    assert _transition(driver_client, booking, "accept").status_code == 200
    assert _pay(passenger_client, booking).status_code == 200
    early = passenger_client.post("/api/v1/ratings/", {"booking": str(booking.id), "score": 5}, format="json")
    assert early.status_code == 400
    assert "Only completed rides" in early.data["detail"]

    assert _start(driver_client, booking).status_code == 200
    assert _transition(driver_client, booking, "complete").status_code == 200

    # Both parties rate; the ratee's Profile.average_rating moves.
    p_rate = passenger_client.post("/api/v1/ratings/", {"booking": str(booking.id), "score": 5}, format="json")
    assert p_rate.status_code == 201
    assert Profile.objects.get(user=driver).average_rating == Decimal("5.00")

    d_rate = driver_client.post("/api/v1/ratings/", {"booking": str(booking.id), "score": 3}, format="json")
    assert d_rate.status_code == 201
    assert Profile.objects.get(user=passenger).average_rating == Decimal("3.00")

    # One rating per booking per rater; outsiders are refused.
    assert passenger_client.post("/api/v1/ratings/", {"booking": str(booking.id), "score": 1}, format="json").status_code == 400
    outsider = APIClient()
    outsider.force_authenticate(User.objects.create_user(phone_number="+21660000018", password="x"))
    assert outsider.post("/api/v1/ratings/", {"booking": str(booking.id), "score": 5}, format="json").status_code == 400

    # GET lists the ratings the caller received.
    received = driver_client.get("/api/v1/ratings/")
    assert received.status_code == 200
    assert [row["score"] for row in received.data["results"]] == [5]


@pytest.mark.django_db
def test_rating_score_bounds_are_enforced():
    driver, vehicle = _make_driver("+21660000019")
    passenger = User.objects.create_user(phone_number="+21660000020", password="x")
    booking = _make_booking(driver, vehicle, passenger)
    passenger_client = APIClient()
    passenger_client.force_authenticate(passenger)
    driver_client = APIClient()
    driver_client.force_authenticate(driver)
    payment_services.create_topup(user=passenger, amount=Decimal("50.000"), gateway=PaymentIntent.Gateway.CASH)
    _transition(driver_client, booking, "accept")
    _pay(passenger_client, booking)
    assert _start(driver_client, booking).status_code == 200
    _transition(driver_client, booking, "complete")

    assert passenger_client.post("/api/v1/ratings/", {"booking": str(booking.id), "score": 0}, format="json").status_code == 400
    assert passenger_client.post("/api/v1/ratings/", {"booking": str(booking.id), "score": 6}, format="json").status_code == 400
