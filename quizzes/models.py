from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.db.models import F, Q


class QuizSession(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="quiz_sessions",
    )
    selected_movies = models.ManyToManyField(
        "movies.Movie",
        related_name="quiz_sessions",
    )
    questions = models.ManyToManyField(
        "vocabulary.VocabularyItem",
        related_name="quiz_sessions",
    )
    total_questions = models.PositiveSmallIntegerField(
        validators=[MinValueValidator(1), MaxValueValidator(25)]
    )
    correct_answers = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ("-created_at",)
        indexes = [
            models.Index(
                fields=("user", "-created_at"),
                name="quiz_user_created_idx",
            )
        ]
        constraints = [
            models.CheckConstraint(
                condition=Q(total_questions__gte=1) & Q(total_questions__lte=25),
                name="quiz_session_question_count_range",
            ),
            models.CheckConstraint(
                condition=Q(correct_answers__lte=F("total_questions")),
                name="quiz_session_correct_not_above_total",
            ),
        ]

    def __str__(self):
        return f"Quiz {self.pk} for {self.user}"

    @property
    def status(self):
        return "completed" if self.completed_at else "active"

    @property
    def score_percentage(self):
        if not self.total_questions:
            return 0
        return round((self.correct_answers / self.total_questions) * 100)


class QuizAttempt(models.Model):
    session = models.ForeignKey(
        QuizSession,
        on_delete=models.CASCADE,
        related_name="attempts",
    )
    vocabulary_item = models.ForeignKey(
        "vocabulary.VocabularyItem",
        on_delete=models.CASCADE,
        related_name="quiz_attempts",
    )
    submitted_answer = models.CharField(max_length=255)
    is_correct = models.BooleanField()
    answered_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("answered_at",)
        constraints = [
            models.UniqueConstraint(
                fields=("session", "vocabulary_item"),
                name="unique_quiz_attempt_per_item",
            )
        ]

    def __str__(self):
        result = "correct" if self.is_correct else "incorrect"
        return f"{self.session_id}: {self.vocabulary_item_id} ({result})"
