from django.contrib import admin

from .models import DeviceToken, Notification


@admin.register(DeviceToken)
class DeviceTokenAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "platform", "created_at"]
    list_filter = ["platform"]
    search_fields = ["user__phone_number", "token"]


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "kind", "title", "read_at", "created_at"]
    list_filter = ["kind"]
    search_fields = ["user__phone_number", "title"]
    readonly_fields = ["kind", "title", "body", "payload", "read_at"]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
