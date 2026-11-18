# Blasa Backend Starter, Part 2

This part completes the bookings dependency chain. It adds the `rides` app, fixes the bookings model, wires the routes, and adds app configs for the remaining apps.

---

## 1. Fix the bookings model

The bookings model uses `PointField`, so it needs the GeoDjango field import, and it references `rides.Ride`, which this part adds.

### `apps/api/apps/bookings/models.py`

```python
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

    class Meta:
        constraints = [
            dj_models.CheckConstraint(check=dj_models.Q(seats__gte=1), name="booking_seats_positive"),
        ]


class BookingEvent(TimeStampedModel):
    booking = models.ForeignKey(Booking, on_delete=models.CASCADE, related_name="events")
    from_status = models.CharField(max_length=30)
    to_status = models.CharField(max_length=30)
    action = models.CharField(max_length=30)
    actor = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)
```

---

## 2. Rides (`apps/rides`)

A ride is a driver's published trip with a fixed number of seats. Seat counts change only through services, and a database check constraint prevents overbooking at the database level.

### `apps/api/apps/rides/__init__.py`

```python
```

### `apps/api/apps/rides/apps.py`

```python
from django.apps import AppConfig


class RidesConfig(AppConfig):
    name = "apps.rides"
    default_auto_field = "django.db.models.BigAutoField"
```

### `apps/api/apps/rides/models.py`

```python
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

    driver = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="rides")
    origin = models.PointField(geography=True, srid=4326)
    destination = models.PointField(geography=True, srid=4326)
    origin_label = models.CharField(max_length=150)  # e.g. "Tunis, Lac 2"
    destination_label = models.CharField(max_length=150)
    departure_at = models.DateTimeField(db_index=True)
    seats_total = models.PositiveSmallIntegerField()
    seats_available = models.PositiveSmallIntegerField()
    price_per_seat = models.DecimalField(max_digits=10, decimal_places=3)
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN, db_index=True)

    class Meta:
        constraints = [
            dj_models.CheckConstraint(
                check=dj_models.Q(seats_available__gte=0)
                & dj_models.Q(seats_available__lte=dj_models.F("seats_total")),
                name="ride_seats_within_capacity",
            ),
            dj_models.CheckConstraint(check=dj_models.Q(seats_total__gte=1), name="ride_seats_positive"),
        ]
        indexes = [models.Index(fields=["status", "departure_at"])]
```

### `apps/api/apps/rides/services.py`

```python
from django.db import transaction
from django.db.models import F
from django.utils import timezone

from core.events import publish

from .models import Ride


@transaction.atomic
def publish_ride(*, driver, **fields) -> Ride:
    if fields["departure_at"] <= timezone.now():
        raise ValueError("Departure must be in the future")
    ride = Ride.objects.create(
        driver=driver,
        seats_available=fields["seats_total"],
        **fields,
    )
    publish("ride.published", ride_id=str(ride.id))
    return ride


@transaction.atomic
def reserve_seats(*, ride_id, seats: int) -> Ride:
    """Atomically decrement seats. The check constraint is the final guard against overbooking."""
    ride = Ride.objects.select_for_update().get(pk=ride_id)
    if ride.status != Ride.Status.OPEN:
        raise ValueError("Ride is not open for booking")
    if ride.seats_available < seats:
        raise ValueError("Not enough seats available")

    ride.seats_available = F("seats_available") - seats
    ride.save(update_fields=["seats_available", "updated_at"])
    ride.refresh_from_db()
    if ride.seats_available == 0:
        ride.status = Ride.Status.FULL
        ride.save(update_fields=["status", "updated_at"])
    return ride
```

### `apps/api/apps/rides/selectors.py`

```python
from django.contrib.gis.db.models.functions import Distance
from django.contrib.gis.measure import D
from django.utils import timezone

from .models import Ride


def open_rides_near(*, origin, radius_km: float = 5):
    """Open rides whose origin is within radius_km of the passenger's pickup point."""
    return (
        Ride.objects.filter(
            status=Ride.Status.OPEN,
            departure_at__gt=timezone.now(),
            origin__distance_lte=(origin, D(km=radius_km)),
        )
        .annotate(distance=Distance("origin", origin))
        .order_by("departure_at", "distance")
    )
```

### `apps/api/apps/rides/admin.py`

```python
from django.contrib import admin

from .models import Ride


@admin.register(Ride)
class RideAdmin(admin.ModelAdmin):
    list_display = ["origin_label", "destination_label", "departure_at", "seats_available", "status"]
    list_filter = ["status"]
    search_fields = ["origin_label", "destination_label", "driver__phone_number"]
```

### `apps/api/apps/rides/serializers.py`

```python
from rest_framework import serializers

from .models import Ride


class RideSerializer(serializers.ModelSerializer):
    class Meta:
        model = Ride
        fields = [
            "id", "origin", "destination", "origin_label", "destination_label",
            "departure_at", "seats_total", "seats_available", "price_per_seat", "status",
        ]
        read_only_fields = ["id", "seats_available", "status"]
```

### `apps/api/apps/rides/views.py`

```python
from rest_framework import mixins, viewsets

from . import services
from .models import Ride
from .serializers import RideSerializer


class RideViewSet(mixins.CreateModelMixin, mixins.RetrieveModelMixin, mixins.ListModelMixin, viewsets.GenericViewSet):
    serializer_class = RideSerializer
    queryset = Ride.objects.all()

    def perform_create(self, serializer):
        ride = services.publish_ride(driver=self.request.user, **serializer.validated_data)
        serializer.instance = ride
```

### `apps/api/apps/rides/urls.py`

```python
from rest_framework.routers import DefaultRouter

from .views import RideViewSet

router = DefaultRouter()
router.register("", RideViewSet, basename="ride")

urlpatterns = router.urls
```

---

## 3. Wire the routes

### `apps/api/api/v1/urls.py`

```python
from django.urls import include, path

urlpatterns = [
    path("users/", include("apps.users.urls")),
    path("rides/", include("apps.rides.urls")),
    path("bookings/", include("apps.bookings.urls")),
]
```

### `apps/api/apps/bookings/apps.py`

```python
from django.apps import AppConfig


class BookingsConfig(AppConfig):
    name = "apps.bookings"
    default_auto_field = "django.db.models.BigAutoField"
```

---

## 4. App config stubs for the remaining apps

Each app needs a package marker and an `apps.py` before its migrations can be created. Create these files with the same pattern, replacing the name in each one.

| App | Files to create | Status in this starter |
|---|---|---|
| `apps/users` | `__init__.py`, `apps.py` | Models present; add `apps.py` |
| `apps/verification` | `__init__.py`, `apps.py` | Models present; add `apps.py` |
| `apps/vehicles` | `__init__.py`, `apps.py`, `models.py` | Needs models for vehicles and Carte Grise link |
| `apps/matching` | `__init__.py`, `apps.py` | Placeholder for geo matching service |
| `apps/wallet` | `__init__.py`, `apps.py` | Models present; add `apps.py` |
| `apps/payments` | `__init__.py`, `apps.py` | Placeholder for payment provider adapters |
| `apps/brands` | `__init__.py`, `apps.py` | Placeholder for sponsorship domain |
| `apps/messaging` | `__init__.py`, `apps.py` | Consumer present; add `apps.py` |
| `apps/notifications` | `__init__.py`, `apps.py` | Placeholder for FCM and SMS |
| `apps/audit` | `__init__.py`, `apps.py` | Models present; add `apps.py` |

Template for each `apps.py`:

```python
from django.apps import AppConfig


class XxxConfig(AppConfig):
    name = "apps.xxx"
    default_auto_field = "django.db.models.BigAutoField"
```

---

## 5. Make the wallet ledger immutable

The `ImmutableQuerySet` from Part 1 was defined but not attached to any model. Attach it to `LedgerEntry` so bulk updates and deletes through the ORM are blocked.

### `apps/api/apps/wallet/models.py` (replace the `LedgerEntry` class)

```python
from apps.wallet.constraints import ImmutableQuerySet


class LedgerEntry(TimeStampedModel):
    transaction = models.ForeignKey(Transaction, on_delete=models.PROTECT, related_name="entries")
    account = models.ForeignKey(Account, on_delete=models.PROTECT, related_name="entries")
    amount = models.DecimalField(max_digits=14, decimal_places=3)

    objects = ImmutableQuerySet.as_manager()

    class Meta:
        indexes = [models.Index(fields=["account", "created_at"])]

    def save(self, *args, **kwargs):
        if self._state.adding is False:
            raise PermissionError("Ledger entries are immutable")
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise PermissionError("Ledger entries are immutable")
```

---

## 6. Test for the seat constraint

### `apps/api/tests/test_ride_seats.py`

```python
from datetime import timedelta
from decimal import Decimal

import pytest
from django.contrib.gis.geos import Point
from django.utils import timezone

from apps.rides import services
from apps.rides.models import Ride
from apps.users.models import User


@pytest.mark.django_db
def test_reserving_all_seats_marks_ride_full():
    driver = User.objects.create_user(phone_number="+21620000001", password="x")
    ride = services.publish_ride(
        driver=driver,
        origin=Point(10.18, 36.80, srid=4326),
        destination=Point(10.64, 35.83, srid=4326),
        origin_label="Tunis",
        destination_label="Sousse",
        departure_at=timezone.now() + timedelta(days=1),
        seats_total=2,
        price_per_seat=Decimal("15.000"),
    )
    ride = services.reserve_seats(ride_id=ride.id, seats=2)
    assert ride.status == Ride.Status.FULL
    assert ride.seats_available == 0
```

---

## 7. Next steps

1. Run `make migrate` after adding the `apps.py` files. Then run `make test`.
2. Create the `vehicles` models next, since a ride needs a verified vehicle with a verified Carte Grise.
3. Connect `bookings.services.apply_action` to `rides.services.reserve_seats` when the booking is accepted, so seats are held at acceptance and released on cancellation.
4. Keep the Law No. 2004-63 review on the legal checklist before any real CIN image is uploaded.
````_
