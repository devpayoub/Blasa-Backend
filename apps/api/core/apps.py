from django.apps import AppConfig


class CoreConfig(AppConfig):
    name = "core"
    verbose_name = "Core"

    def ready(self):
        # Sentry reads settings after configuration is complete; doing it at
        # settings import time would touch the settings object before Django has
        # finished building it.
        from .observability import init_sentry

        init_sentry()
