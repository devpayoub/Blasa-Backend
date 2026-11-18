class GatewayError(Exception):
    """The gateway is misconfigured or rejected a request; the view maps this to a client-facing error."""


class BaseGateway:
    """Contract for payment gateway adapters: create an intent, parse an inbound webhook."""

    name = "base"

    def create_intent(self, intent) -> dict:
        """Ask the gateway to accept the intent. Returns client-facing data (payment URL, ...)."""
        raise NotImplementedError

    def parse_webhook(self, body: bytes, signature: str | None) -> dict:
        """Verify the webhook signature and return {event_id, event, reference, amount, currency, payload}."""
        raise NotImplementedError
