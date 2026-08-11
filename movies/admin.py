from django.contrib import admin

from .models import Movie


@admin.register(Movie)
class MovieAdmin(admin.ModelAdmin):
    list_display = ("title", "release_year", "user", "created_at")
    list_filter = ("release_year",)
    search_fields = ("title", "user__username", "user__email")
    autocomplete_fields = ("user",)
