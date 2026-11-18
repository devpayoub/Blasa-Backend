from django.contrib import admin

from .models import Rating


@admin.register(Rating)
class RatingAdmin(admin.ModelAdmin):
    list_display = ["id", "booking", "rater", "ratee", "score", "created_at"]
    search_fields = ["rater__phone_number", "ratee__phone_number"]

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
