from django.contrib import admin

from .models import VocabularyItem


@admin.register(VocabularyItem)
class VocabularyItemAdmin(admin.ModelAdmin):
    list_display = (
        "word_or_phrase",
        "movie",
        "type",
        "cefr_level",
        "created_at",
    )
    list_filter = ("cefr_level", "type", "created_at")
    list_select_related = ("movie",)
    search_fields = (
        "word_or_phrase",
        "definition_en",
        "movie__title",
    )
    readonly_fields = ("created_at", "updated_at")
