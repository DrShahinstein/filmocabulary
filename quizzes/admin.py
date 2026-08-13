from django.contrib import admin

from .models import UserWordStatus


@admin.register(UserWordStatus)
class UserWordStatusAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "vocabulary_item",
        "status",
        "correct_count",
        "wrong_count",
        "last_tested_at",
    )
    list_filter = ("status", "last_tested_at")
    search_fields = (
        "user__username",
        "vocabulary_item__word_or_phrase",
        "vocabulary_item__movie__title",
    )
    list_select_related = ("user", "vocabulary_item", "vocabulary_item__movie")
    readonly_fields = ("last_tested_at",)
