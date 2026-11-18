from .base import BaseGateway, GatewayError


class CashGateway(BaseGateway):
    """Cash settles in hand: a topup is credited immediately, no webhook involved."""

    name = "cash"

    def create_intent(self, intent) -> dict:
        return {"method": "cash", "instructions": "Settled in cash; the wallet is credited immediately."}

    def parse_webhook(self, body: bytes, signature: str | None) -> dict:
        raise GatewayError("Cash does not accept webhooks")
