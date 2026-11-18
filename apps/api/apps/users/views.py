from drf_spectacular.utils import extend_schema
from rest_framework import generics, serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from . import avatar
from .models import InterestTag, User
from .serializers import InterestTagSerializer, UserPublicProfileSerializer, UserSerializer
from .services import upgrade_to_driver
from .summaries import profile_detail


class InterestTagListView(generics.ListAPIView):
    """The personal interest taxonomy members pick from."""

    serializer_class = InterestTagSerializer
    queryset = InterestTag.objects.filter(is_active=True)
    pagination_class = None


class MeView(generics.RetrieveUpdateAPIView):
    serializer_class = UserSerializer

    def get_object(self):
        return self.request.user


class UserPublicProfileView(APIView):
    """Trust profile of another participant, reachable from a ride, request or booking.

    Only members with an account can read it, and it never carries contact
    details, identity documents or wallet data. Review comments are returned to
    the member themselves and to people who shared a booking with them.
    """

    @extend_schema(responses={200: UserPublicProfileSerializer})
    def get(self, request, user_id):
        user = (
            User.objects.select_related("profile").prefetch_related("profile__interests").filter(pk=user_id).first()
        )
        if user is None or not user.is_active:
            return Response({"detail": "Not found"}, status=status.HTTP_404_NOT_FOUND)
        return Response(UserPublicProfileSerializer(profile_detail(user, request.user)).data)


class BecomeDriverView(APIView):
    @extend_schema(request=None, responses={200: UserSerializer})
    def post(self, request):
        try:
            user = upgrade_to_driver(request.user)
        except ValueError as exc:
            return Response({"detail": str(exc)}, status=status.HTTP_400_BAD_REQUEST)
        return Response(UserSerializer(user).data)


class AvatarUploadRequestSerializer(serializers.Serializer):
    file_name = serializers.CharField(max_length=255)
    content_type = serializers.ChoiceField(choices=list(avatar.TYPES))
    size_bytes = serializers.IntegerField(min_value=1, max_value=avatar.MAX_BYTES)


class AvatarUploadResponseSerializer(serializers.Serializer):
    upload_url = serializers.URLField()
    upload_token = serializers.CharField()


class AvatarConfirmSerializer(serializers.Serializer):
    upload_token = serializers.CharField(max_length=2048)


class AvatarDownloadSerializer(serializers.Serializer):
    download_url = serializers.URLField()
    expires_in = serializers.IntegerField()


class AvatarUploadView(APIView):
    @extend_schema(request=AvatarUploadRequestSerializer, responses={201: AvatarUploadResponseSerializer})
    def post(self, request):
        serializer = AvatarUploadRequestSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            upload = avatar.request_upload(user=request.user, **serializer.validated_data)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return Response(upload, status=status.HTTP_201_CREATED)


class AvatarConfirmView(APIView):
    @extend_schema(request=AvatarConfirmSerializer, responses={200: UserSerializer})
    def post(self, request):
        serializer = AvatarConfirmSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            user = avatar.confirm_upload(user=request.user, **serializer.validated_data)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return Response(UserSerializer(user).data)


class AvatarDownloadView(APIView):
    @extend_schema(responses={200: AvatarDownloadSerializer})
    def get(self, request):
        try:
            result = avatar.download_url(request.user)
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc
        return Response(result)
