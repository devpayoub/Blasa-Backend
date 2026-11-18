from .base import BaseGateway, GatewayError
from .cash import CashGateway
from .flouci import FlouciGateway

_REGISTRY: dict[str, type[BaseGateway]] = {
    CashGateway.name: CashGateway,
    FlouciGateway.name: FlouciGateway,
}


def get_gateway(name: str) -> BaseGateway:
    try:
        # Model choices are uppercase (CASH/FLOUCI); registry keys are lowercase.
        return _REGISTRY[name.lower()]()
    except KeyError:
        raise GatewayError(f"Unknown gateway '{name}'") from None


__all__ = ["BaseGateway", "GatewayError", "get_gateway"]
