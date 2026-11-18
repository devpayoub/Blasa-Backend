from rest_framework import serializers

from . import services
from .models import Conversation, Message


class ConversationSerializer(serializers.ModelSerializer):
    booking = serializers.PrimaryKeyRelatedField(read_only=True)
    participants = serializers.SerializerMethodField()
    booking_status = serializers.CharField(source="booking.status", read_only=True)
    can_send = serializers.SerializerMethodField()

    class Meta:
        model = Conversation
        fields = ["id", "booking", "participants", "booking_status", "can_send", "created_at"]

    def get_participants(self, obj) -> list:
        booking = obj.booking
        return [str(booking.passenger_id), str(booking.ride.driver_id)]

    def get_can_send(self, obj) -> bool:
        return services.can_send(obj.booking)


class MessageSerializer(serializers.ModelSerializer):
    conversation = serializers.PrimaryKeyRelatedField(read_only=True, pk_field=serializers.UUIDField())
    sender = serializers.PrimaryKeyRelatedField(read_only=True, pk_field=serializers.UUIDField())

    class Meta:
        model = Message
        fields = ["id", "conversation", "sender", "body", "created_at"]
        read_only_fields = ["id", "conversation", "sender", "created_at"]
