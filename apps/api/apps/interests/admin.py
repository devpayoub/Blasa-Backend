from django.contrib import admin

from .models import Interest


@admin.register(Interest)
class InterestAdmin(admin.ModelAdmin):
    list_display = ["id", "passenger", "origin_label", "destination_label", "earliest_departure", "is_active"]
    list_filter = ["is_active"]
    search_fields = ["passenger__phone_number"]
