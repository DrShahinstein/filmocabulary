from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class UserWordStatus(models.Model):
    class Status(models.TextChoices):
        NEW = "new", "New"
        LEARNING = "learning", "Learning"
        MASTERED = "mastered", "Mastered"

    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="word_statuses",
    )
    vocabulary_item = models.ForeignKey(
        "vocabulary.VocabularyItem",
        on_delete=models.CASCADE,
        related_name="user_statuses",
    )
    status = models.CharField(
        max_length=10,
        choices=Status.choices,
        default=Status.NEW,
    )
    wrong_count = models.PositiveIntegerField(default=0)
    correct_count = models.PositiveIntegerField(default=0)
    last_tested_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ("status", "vocabulary_item__word_or_phrase", "pk")
        constraints = (
            models.UniqueConstraint(
                fields=("user", "vocabulary_item"),
                name="unique_user_vocabulary_status",
            ),
        )
        indexes = (
            models.Index(
                fields=("user", "status"),
                name="word_status_user_state_idx",
            ),
            models.Index(
                fields=("user", "last_tested_at"),
                name="word_status_user_tested_idx",
            ),
        )
        verbose_name_plural = "user word statuses"

    def __str__(self):
        return f"{self.user}: {self.vocabulary_item} ({self.status})"

    def clean(self):
        super().clean()
        if (
            self.user_id
            and self.vocabulary_item_id
            and self.vocabulary_item.movie.user_id != self.user_id
        ):
            raise ValidationError(
                {"vocabulary_item": "Track only vocabulary from the user's library."}
            )
