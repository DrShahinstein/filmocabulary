from django.contrib import admin

from .models import QuizAttempt, QuizSession


class QuizAttemptInline(admin.TabularInline):
    model = QuizAttempt
    extra = 0
    readonly_fields = (
        "vocabulary_item",
        "submitted_answer",
        "is_correct",
        "answered_at",
    )
    can_delete = False


@admin.register(QuizSession)
class QuizSessionAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "user",
        "created_at",
        "completion_status",
        "correct_answers",
        "total_questions",
    )
    list_filter = ("created_at", "completed_at")
    search_fields = ("user__username", "selected_movies__title")
    readonly_fields = ("created_at", "completed_at")
    filter_horizontal = ("selected_movies", "questions")
    inlines = (QuizAttemptInline,)

    @admin.display(description="Status")
    def completion_status(self, obj):
        return obj.status


@admin.register(QuizAttempt)
class QuizAttemptAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "session",
        "vocabulary_item",
        "is_correct",
        "answered_at",
    )
    list_filter = ("is_correct", "answered_at")
    search_fields = (
        "session__user__username",
        "vocabulary_item__word_or_phrase",
        "submitted_answer",
    )
    list_select_related = ("session", "vocabulary_item")
    readonly_fields = ("answered_at",)

