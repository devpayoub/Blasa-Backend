from django.apps import AppConfig


class IncentivesConfig(AppConfig):
    name = "apps.incentives"
    default_auto_field = "django.db.models.BigAutoField"

    def ready(self):
        from core.events import subscribe

        from .services import handle_eligibility_event

        for event in ("verification.changed", "user.became_driver", "vehicle.changed"):
            subscribe(event, handle_eligibility_event)
