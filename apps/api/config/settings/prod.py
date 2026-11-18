"""Production settings. Everything here assumes a TLS-terminating reverse proxy
unless the platform itself terminates TLS (then remove SECURE_PROXY_SSL_HEADER).
"""

from .base import *  # noqa: F401,F403

DEBUG = False

# --- TLS / transport security ---------------------------------------------
SECURE_SSL_REDIRECT = True
SECURE_HSTS_SECONDS = 31536000  # 1 year
SECURE_HSTS_INCLUDE_SUBDOMAINS = True
SECURE_HSTS_PRELOAD = True
# Trust the proxy's proto header (nginx / load balancer terminates TLS).
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# --- Cookies / browser hardening ------------------------------------------
SESSION_COOKIE_SECURE = True
CSRF_COOKIE_SECURE = True
SESSION_COOKIE_HTTPONLY = True
CSRF_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Lax"
SECURE_REFERRER_POLICY = "same-origin"
SECURE_CROSS_ORIGIN_OPENER_POLICY = "same-origin"
X_FRAME_OPTIONS = "DENY"

# --- Hosts ----------------------------------------------------------------
if not ALLOWED_HOSTS:  # noqa: F405
    raise RuntimeError("DJANGO_ALLOWED_HOSTS must be set in production")

# --- Identity document uploads --------------------------------------------
# Law 2004-63 gate: stays OFF until docs/legal/law-2004-63-compliance.md is
# signed off by counsel. Flip only via the environment variable.
IDENTITY_DOCUMENT_UPLOADS_ENABLED = env.bool(  # noqa: F405
    "IDENTITY_DOCUMENT_UPLOADS_ENABLED", default=False
)

# --- Error tracking --------------------------------------------------------
# Initialised in base.py from SENTRY_DSN so web, worker and beat all report the
# same way. Kept as an explicit note because production is where it matters.
SENTRY_ENVIRONMENT = env("SENTRY_ENVIRONMENT", default="production")  # noqa: F405
