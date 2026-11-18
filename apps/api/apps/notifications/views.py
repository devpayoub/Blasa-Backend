from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView

from core.schema import schema_or_anonymous

from . import services
from .models import Notification
from .serializers import (
    DeviceRegistrationSerializer,
    DeviceTokenSerializer,
    DeviceUnregisterSerializer,
    NotificationCountSerializer,
    NotificationPreferenceSerializer,
    NotificationReadAllSerializer,
    NotificationSerializer,
)


@extend_schema_view(list=extend_schema(parameters=[OpenApiParameter("unread", bool)]))
class NotificationViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = NotificationSerializer

    def get_queryset(self):
        if schema_or_anonymous(self):
            return Notification.objects.none()
        queryset = Notification.objects.filter(user=self.request.user)
        if self.request.query_params.get("unread") == "true":
            queryset = queryset.filter(read_at__isnull=True)
        return queryset

    @extend_schema(responses=NotificationCountSerializer)
    @action(detail=False, methods=["get"], url_path="unread-count")
    def unread_count(self, request):
        return Response({"count": Notification.objects.filter(user=request.user, read_at__isnull=True).count()})

    @extend_schema(request=None, responses=NotificationReadAllSerializer)
    @action(detail=False, methods=["post"], url_path="read-all")
    def read_all(self, request):
        count = Notification.objects.filter(user=request.user, read_at__isnull=True).update(read_at=timezone.now())
        return Response({"updated": count})


class NotificationReadView(APIView):
    @extend_schema(request=None, responses=NotificationSerializer)
    def post(self, request, notification_id):
        notification = Notification.objects.filter(pk=notification_id, user=request.user).first()
        if notification is None:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        services.mark_read(notification=notification, user=request.user)
        return Response(NotificationSerializer(notification).data)


class NotificationPreferencesView(APIView):
    @extend_schema(responses=NotificationPreferenceSerializer)
    def get(self, request):
        return Response(NotificationPreferenceSerializer(services.preferences_for(request.user)).data)

    @extend_schema(request=NotificationPreferenceSerializer, responses=NotificationPreferenceSerializer)
    def patch(self, request):
        serializer = NotificationPreferenceSerializer(services.preferences_for(request.user), data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data)


class DeviceRegistryView(APIView):
    @extend_schema(request=DeviceRegistrationSerializer, responses={201: DeviceTokenSerializer})
    def post(self, request):
        serializer = DeviceRegistrationSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            device = services.register_device(user=request.user, **serializer.validated_data)
        except PermissionError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_403_FORBIDDEN)
        return Response(DeviceTokenSerializer(device).data, status=status.HTTP_201_CREATED)

    @extend_schema(request=DeviceUnregisterSerializer, responses={204: None})
    def delete(self, request):
        serializer = DeviceUnregisterSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.unregister_device(user=request.user, **serializer.validated_data)
        return Response(status=status.HTTP_204_NO_CONTENT)
