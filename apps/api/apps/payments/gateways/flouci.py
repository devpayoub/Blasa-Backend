import hashlib
import hmac
import json
import urllib.request

from django.conf import settings

from .base import BaseGateway, GatewayError


class FlouciGateway(BaseGateway):
    """Flouci online payments. HTTP via stdlib urllib — no extra dependency in the image."""

    name = "flouci"

    def _config(self) -> tuple[str, str]:
        base_url = getattr(settings, "FLUOCI_BASE_URL", "")
        app_key = getattr(settings, "FLUOCI_APP_KEY", "")
        private_key = getattr(settings, "FLUOCI_PRIVATE_KEY", "")
        if not (base_url and app_key and private_key):
            raise GatewayError("Flouci is not configured")
        return base_url, private_key

    def create_intent(self, intent) -> dict:
        base_url, private_key = self._config()
        payload = json.dumps(
            {
                "amount": str(intent.amount),
                "currency": intent.currency,
                "app_key": getattr(settings, "FLUOCI_APP_KEY", ""),
                "developer_tracking_id": str(intent.id),
            }
        ).encode()
        request = urllib.request.Request(
            f"{base_url}/api/payment/",
            data=payload,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {private_key}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=15) as response:
                result = json.load(response)
        except Exception as exc:  # network / HTTP / decode failures all become a GatewayError
            raise GatewayError(f"Flouci rejected the payment request: {exc}") from exc

        # Flouci returns {"result": {...}}; tolerate a flat body too.
        body = result.get("result", result)
        intent.gateway_reference = str(body.get("id", ""))
        intent.save(update_fields=["gateway_reference", "updated_at"])
        return {
            "payment_url": body.get("redirect_link") or body.get("url"),
            "gateway_reference": intent.gateway_reference,
        }

    def parse_webhook(self, body: bytes, signature: str | None) -> dict:
        secret = getattr(settings, "FLUOCI_WEBHOOK_SECRET", "")
        if not secret:
            raise GatewayError("Flouci webhook secret is not configured")
        expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        if not signature or not hmac.compare_digest(expected, signature):
            raise GatewayError("Invalid webhook signature")

        payload = json.loads(body)
        try:
            event_id = payload["event_id"]
            event = payload["event"]
            reference = payload["data"]["payment"]["id"]
        except (KeyError, TypeError) as exc:
            raise GatewayError("Malformed webhook payload") from exc
        payment = payload["data"]["payment"]
        return {
            "event_id": event_id,
            "event": event,
            "reference": reference,
            "amount": payment.get("amount"),
            "currency": payment.get("currency"),
            "payload": payload,
        }
