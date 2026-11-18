"""Staff-decision audit trail.

record_action() writes one immutable row per privileged mutation. It stores the
actor, the object reference and the decision metadata only: never request
bodies, OTPs, JWTs, document contents or payment credentials.
"""

from .models import AuditLog

# Metadata keys that must never reach the audit trail, even if a caller passes them.
_FORBIDDEN_KEYS = {
    "password", "token", "access", "refresh", "authorization", "otp", "code",
    "code_hash", "secret", "signature", "card", "iban", "document", "content",
}


def _safe(metadata: dict | None) -> dict:
    if not metadata:
        return {}
    return {
        key: (value if isinstance(value, (str, int, float, bool, type(None), list)) else str(value))
        for key, value in metadata.items()
        if key.lower() not in _FORBIDDEN_KEYS
    }


def record_action(
    *, actor, action: str, object_type: str, object_id, reason: str = "", metadata: dict | None = None,
) -> AuditLog:
    """Record a deliberate staff decision. Call inside the mutation's transaction."""
    return AuditLog.objects.create(
        actor=actor,
        method="DECISION",
        path=action,
        status_code=200,
        action=action[:60],
        object_type=object_type[:40],
        object_id=str(object_id)[:64],
        reason=(reason or "")[:200],
        metadata=_safe(metadata),
    )


def history_for(object_type: str, object_id) -> list[AuditLog]:
    return list(
        AuditLog.objects.filter(object_type=object_type, object_id=str(object_id)).select_related("actor")
    )
