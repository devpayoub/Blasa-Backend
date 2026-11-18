from .base import *  # noqa: F401,F403

DEBUG = False
CHANNEL_LAYERS = {"default": {"BACKEND": "channels.layers.InMemoryChannelLayer"}}
CELERY_TASK_ALWAYS_EAGER = True
# Tests never reach a real provider: mail lands in mail.outbox and SMS is a stub.
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
SMS_PROVIDER = "console"
SENTRY_DSN = ""
# The throttle cache survives between tests (unlike the DB), so counts leak across tests.
# Classes cleared for the defaults, and per-view scoped rates raised to no-op the counters.
REST_FRAMEWORK = {  # noqa: F405
    **REST_FRAMEWORK,  # noqa: F405
    "DEFAULT_THROTTLE_CLASSES": [],
    "DEFAULT_THROTTLE_RATES": {**REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"], "otp": "100000/min", "login": "100000/min"},  # noqa: F405
}
