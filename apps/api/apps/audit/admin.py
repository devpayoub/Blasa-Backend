from django.contrib import admin

from .models import AuditLog


@admin.register(AuditLog)
class AuditLogAdmin(admin.ModelAdmin):
    """Read-only staff audit trail: decisions plus /admin/ request traces."""

    list_display = ["created_at", "actor", "action", "object_type", "object_id", "reason", "method", "status_code"]
    list_filter = ["action", "object_type", "method"]
    search_fields = ["object_id", "reason", "actor__phone_number", "actor__email", "path"]
    date_hierarchy = "created_at"
    ordering = ["-created_at"]
    readonly_fields = [
        "created_at", "updated_at", "actor", "action", "object_type", "object_id",
        "reason", "metadata", "method", "path", "status_code", "request_id", "ip_hash",
    ]
    fields = readonly_fields

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
