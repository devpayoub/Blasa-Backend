from django.contrib import admin

from .models import Vehicle


@admin.register(Vehicle)
class VehicleAdmin(admin.ModelAdmin):
    list_display = ["make", "model", "plate_number", "driver", "is_active", "is_verified"]
    list_filter = ["is_active"]
    search_fields = ["plate_number", "driver__phone_number"]
