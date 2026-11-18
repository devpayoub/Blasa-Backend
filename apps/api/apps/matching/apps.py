from django.apps import AppConfig


class MatchingConfig(AppConfig):
    name = "apps.matching"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        import logging

        logger = logging.getLogger("blasa.matching")
        from core.events import subscribe

        from .tasks import match_interest_to_rides, match_ride_to_interests

        def _safe_enqueue(task, **kwargs):
            # The publisher's transaction must never fail because a background
            # consumer could not be dispatched.
            try:
                task.delay(**kwargs)
            except Exception:  # noqa: BLE001
                logger.exception("Failed to dispatch %s for %s", task.name, kwargs)

        subscribe("ride.published", lambda ride_id, **_: _safe_enqueue(match_ride_to_interests, ride_id=ride_id))
        subscribe("ride.updated", lambda ride_id, **_: _safe_enqueue(match_ride_to_interests, ride_id=ride_id))
        subscribe("interest.created", lambda interest_id, **_: _safe_enqueue(match_interest_to_rides, interest_id=interest_id))
