from django.contrib.auth import get_user_model
from django.db import transaction

from apps.verification.models import VerificationDocument
from core.events import publish

from .models import Vehicle

_UNSET = object()


def _require_driver(driver):
    if not driver.is_active or driver.role != driver.Role.DRIVER:
        raise ValueError("Only active drivers can manage vehicles")


def _validate_carte_grise(driver, document):
    if document is None:
        return
    if document.doc_type != VerificationDocument.DocType.CARTE_GRISE:
        raise ValueError("The document must be a Carte Grise")
    if document.user_id != driver.id:
        raise ValueError("The Carte Grise must belong to the driver")


@transaction.atomic
def register_vehicle(*, driver, carte_grise=None, **fields) -> Vehicle:
    driver = get_user_model().objects.select_for_update().get(pk=driver.pk)
    _require_driver(driver)
    _validate_carte_grise(driver, carte_grise)
    vehicle = Vehicle.objects.create(driver=driver, carte_grise=carte_grise, **fields)
    publish("vehicle.changed", vehicle_id=str(vehicle.pk), driver_id=driver.pk)
    return vehicle


@transaction.atomic
def update_vehicle(*, vehicle: Vehicle, driver, carte_grise=_UNSET, **fields) -> Vehicle:
    driver = get_user_model().objects.select_for_update().get(pk=driver.pk)
    _require_driver(driver)
    if vehicle.driver_id != driver.pk:
        raise ValueError("The vehicle must belong to the driver")
    vehicle = Vehicle.objects.select_for_update().get(pk=vehicle.pk, driver=driver)
    if carte_grise is not _UNSET:
        _validate_carte_grise(driver, carte_grise)
    allowed = {"make", "model", "color", "plate_number", "seats", "is_active"}
    if fields.keys() - allowed:
        raise ValueError("Invalid vehicle fields")
    # Validate every association and authorization before touching any persisted field.
    for field, value in fields.items():
        setattr(vehicle, field, value)
    changed = list(fields)
    if carte_grise is not _UNSET:
        vehicle.carte_grise = carte_grise
        changed.append("carte_grise")
    if changed:
        vehicle.save(update_fields=[*changed, "updated_at"])
        publish("vehicle.changed", vehicle_id=str(vehicle.pk), driver_id=driver.pk)
    return vehicle


@transaction.atomic
def attach_carte_grise(*, vehicle: Vehicle, document) -> Vehicle:
    return update_vehicle(vehicle=vehicle, driver=vehicle.driver, carte_grise=document)
