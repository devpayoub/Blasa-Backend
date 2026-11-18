from collections import defaultdict
from typing import Callable

from django.db import transaction

_handlers: dict[str, list[Callable]] = defaultdict(list)
_transactional_handlers: dict[str, list[Callable]] = defaultdict(list)


def subscribe(event_name: str, handler: Callable, *, transactional: bool = False) -> None:
    registry = _transactional_handlers if transactional else _handlers
    registry[event_name].append(handler)


def _dispatch(registry, event_name, payload):
    for handler in registry[event_name]:
        handler(**payload)


def publish(event_name: str, **payload) -> None:
    _dispatch(_transactional_handlers, event_name, payload)
    _dispatch(_handlers, event_name, payload)


def publish_on_commit(event_name: str, **payload) -> None:
    # Inbox/outbox writes share the business transaction; external work waits for commit.
    _dispatch(_transactional_handlers, event_name, payload)
    transaction.on_commit(lambda: _dispatch(_handlers, event_name, payload))
