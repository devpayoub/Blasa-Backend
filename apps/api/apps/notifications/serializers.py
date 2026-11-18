from django.conf import settings
from rest_framework import serializers

from .models import DeviceToken, Notification, NotificationPreference


class NotificationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Notification
        fields = ["id", "kind", "title", "body", "payload", "read_at", "created_at"]
        read_only_fields = fields


class DeviceTokenSerializer(serializers.ModelSerializer):
    class Meta:
        model = DeviceToken
        fields = ["token", "platform", "provider"]


class DeviceRegistrationSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=255)
    platform = serializers.CharField(default=DeviceToken.Platform.ANDROID)
    provider = serializers.ChoiceField(choices=DeviceToken.Provider.choices, default=DeviceToken.Provider.FCM)

    def validate_platform(self, value):
        value = value.upper()
        if value not in DeviceToken.Platform.values:
            raise serializers.ValidationError("Choose IOS, ANDROID or WEB.")
        return value

    def validate(self, attrs):
        expo = attrs["token"].startswith(("ExpoPushToken[", "ExponentPushToken[")) and attrs["token"].endswith("]")
        if expo != (attrs["provider"] == DeviceToken.Provider.EXPO):
            raise serializers.ValidationError("Use the matching push transport for this token.")
        if attrs["provider"] == DeviceToken.Provider.EXPO and attrs["platform"] == DeviceToken.Platform.WEB:
            raise serializers.ValidationError("Expo push supports native devices only.")
        return attrs


class DeviceUnregisterSerializer(serializers.Serializer):
    token = serializers.CharField(max_length=255)


class NotificationCountSerializer(serializers.Serializer):
    count = serializers.IntegerField(read_only=True)


class NotificationReadAllSerializer(serializers.Serializer):
    updated = serializers.IntegerField(read_only=True)


class NotificationPreferenceSerializer(serializers.ModelSerializer):
    capabilities = serializers.SerializerMethodField()

    class Meta:
        model = NotificationPreference
        fields = ["push_enabled", "email_enabled", "sms_enabled", "promotions_enabled", "capabilities"]

    def get_capabilities(self, obj) -> dict:
        return {"expo": settings.EXPO_PUSH_ENABLED, "fcm": bool(settings.FCM_CREDENTIALS_PATH),
                "email": settings.NOTIFICATION_EMAIL_ENABLED, "sms": False}
