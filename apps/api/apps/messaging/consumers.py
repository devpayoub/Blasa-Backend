from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from channels.generic.websocket import AsyncJsonWebsocketConsumer
from django.utils import timezone
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.tokens import AccessToken

from apps.users.models import User

from . import services


@database_sync_to_async
def _user_from_token(token_str):
    """Authenticate a JWT query token without retaining or logging the query."""
    try:
        token = AccessToken(token_str)
    except TokenError:
        return None
    user_id = User.objects.filter(pk=token["user_id"]).values_list("pk", flat=True).first()
    return (user_id, token["exp"]) if user_id else None


@database_sync_to_async
def _eligible(conversation_id, user_id):
    try:
        services.check_membership(conversation=conversation_id, user=user_id, for_send=True)
    except services.NotConversationMember:
        return False
    return True


@database_sync_to_async
def _persist_message(conversation_id, user_id, body):
    services.post_message(conversation=conversation_id, sender=user_id, body=body)


class ChatConsumer(AsyncJsonWebsocketConsumer):
    async def connect(self):
        user = self.scope.get("user")
        user_id = user.pk if user and user.is_authenticated else None
        self.token_expires_at = None
        if user_id is None:
            query = parse_qs(self.scope.get("query_string", b"").decode())
            token = (query.get("token") or [None])[0]
            authenticated = await _user_from_token(token) if token else None
            if authenticated:
                user_id, self.token_expires_at = authenticated
        self.scope["query_string"] = b""
        if user_id is None:
            await self.close(code=4401)
            return

        conversation_id = self.scope["url_route"]["kwargs"]["conversation_id"]
        if not await _eligible(conversation_id, user_id):
            await self.close(code=4403)
            return

        self.user_id = user_id
        self.conversation_id = conversation_id
        self.authorized = True
        self.group_name = f"chat_{conversation_id}"
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        await self.accept()

    async def disconnect(self, code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def _close_forbidden(self):
        self.authorized = False
        await self.channel_layer.group_discard(self.group_name, self.channel_name)
        await self.close(code=4403)

    async def _token_is_valid(self):
        if self.token_expires_at is not None and timezone.now().timestamp() >= self.token_expires_at:
            self.authorized = False
            await self.channel_layer.group_discard(self.group_name, self.channel_name)
            await self.close(code=4401)
            return False
        return True

    async def receive_json(self, content, **kwargs):
        if not getattr(self, "authorized", False) or not await self._token_is_valid():
            return
        body = content.get("body") if isinstance(content, dict) else None
        try:
            await _persist_message(self.conversation_id, self.user_id, body)
        except services.NotConversationMember:
            await self._close_forbidden()
        except ValueError:
            await self.send_json({"error": "Message rejected"})

    async def chat_message(self, event):
        if not getattr(self, "authorized", False) or not await self._token_is_valid():
            return
        if not await _eligible(self.conversation_id, self.user_id):
            await self._close_forbidden()
            return
        await self.send_json({"type": "chat.message", "message": event["message"]})
