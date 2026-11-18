"""Optional Sentry wiring.

Initialised from settings so every process (web, worker, beat, channels) reports
the same way. Without `SENTRY_DSN` this is a no-op, which keeps local tests and
the mock-first frontend workflow offline-friendly.
"""

from __future__ import annotations

import logging

from django.conf import settings

logger = logging.getLogger("blasa.observability")

SENSITIVE_KEYS = {
    "password",
    "new_password",
    "current_password",
    "token",
    "access",
    "refresh",
    "code",
    "otp",
    "authorization",
    "api-key",
    "api_key",
    "secret",
}


def _scrub_mapping(mapping):
    if not isinstance(mapping, dict):
        return mapping
    return {
        key: "[Filtered]" if str(key).lower() in SENSITIVE_KEYS else _scrub_mapping(value)
        for key, value in mapping.items()
    }


def scrub_event(event, _hint=None):
    """Drop credentials and OTP codes before an event leaves the process."""
    request = event.get("request")
    if isinstance(request, dict):
        headers = request.get("headers")
        if isinstance(headers, dict):
            request["headers"] = {
                key: ("[Filtered]" if str(key).lower() in SENSITIVE_KEYS else value)
                for key, value in headers.items()
            }
        if "data" in request:
            request["data"] = _scrub_mapping(request["data"])
        request.pop("cookies", None)
    return event


def init_sentry() -> bool:
    dsn = getattr(settings, "SENTRY_DSN", "")
    if not dsn:
        return False
    try:
        import sentry_sdk
        from sentry_sdk.integrations.celery import CeleryIntegration
        from sentry_sdk.integrations.django import DjangoIntegration
    except ImportError:  # pragma: no cover - prod.txt installs it
        logger.warning("SENTRY_DSN is set but sentry-sdk is not installed")
        return False

    integrations = [DjangoIntegration(), CeleryIntegration()]
    try:
        from sentry_sdk.integrations.redis import RedisIntegration

        integrations.append(RedisIntegration())
    except ImportError:  # pragma: no cover - optional extra
        pass

    sentry_sdk.init(
        dsn=dsn,
        environment=getattr(settings, "SENTRY_ENVIRONMENT", "production"),
        release=getattr(settings, "SENTRY_RELEASE", None) or None,
        integrations=integrations,
        traces_sample_rate=getattr(settings, "SENTRY_TRACES_RATE", 0.05),
        send_default_pii=False,
        before_send=scrub_event,
    )
    logger.info("Sentry initialised for environment %s", getattr(settings, "SENTRY_ENVIRONMENT", "production"))
    return True
