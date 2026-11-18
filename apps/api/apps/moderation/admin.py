from django.contrib import admin

from .models import Ban, Report


@admin.register(Report)
class ReportAdmin(admin.ModelAdmin):
    list_display = ["id", "reporter", "target_type", "target_id", "reason", "status", "created_at"]
    list_filter = ["status", "reason"]
    search_fields = ["target_id", "details"]


@admin.register(Ban)
class BanAdmin(admin.ModelAdmin):
    list_display = ["id", "user", "issued_by", "revoked_at", "created_at"]
    list_filter = ["revoked_at"]
    search_fields = ["user__phone_number", "reason"]
