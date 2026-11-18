from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.response import Response
from rest_framework.views import APIView

from core.schema import schema_or_anonymous

from . import services
from .models import Conversation
from .serializers import ConversationSerializer, MessageSerializer


class ConversationViewSet(
    mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet
):
    serializer_class = ConversationSerializer

    def get_queryset(self):
        if schema_or_anonymous(self):
            return Conversation.objects.none()
        return services.readable_conversations(self.request.user)


class MessageHistoryView(APIView):
    """Chat history for a conversation the caller participates in."""

    @extend_schema(responses=MessageSerializer(many=True))
    def get(self, request, conversation_id):
        conversation = get_object_or_404(Conversation, pk=conversation_id)
        try:
            conversation = services.check_membership(conversation=conversation, user=request.user)
        except services.NotConversationMember as exc:
            return Response({"detail": str(exc)}, status=403)
        messages = conversation.messages.select_related("sender").all()
        return Response(MessageSerializer(messages, many=True).data)

    @extend_schema(request=MessageSerializer, responses={201: MessageSerializer})
    def post(self, request, conversation_id):
        """REST fallback for sending a message (the websocket is the primary channel)."""
        conversation = get_object_or_404(Conversation, pk=conversation_id)
        try:
            message = services.post_message(
                conversation=conversation, sender=request.user, body=request.data.get("body", "")
            )
        except services.NotConversationMember as exc:
            return Response({"detail": str(exc)}, status=403)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(MessageSerializer(message).data, status=status.HTTP_201_CREATED)
