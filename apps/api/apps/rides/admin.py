from django.contrib import admin

from .models import Ride


@admin.register(Ride)
class RideAdmin(admin.ModelAdmin):
    list_display = ["origin_label", "destination_label", "departure_at", "seats_available", "status"]
    list_filter = ["status"]
    search_fields = ["origin_label", "destination_label", "driver__phone_number"]
