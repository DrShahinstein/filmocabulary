from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone


CEFR_LEVEL_SCORES = {"B1": 1, "B2": 2, "C1": 3, "C2": 4}
CEFR_SCORE_LEVELS = {score: level for level, score in CEFR_LEVEL_SCORES.items()}


def current_year() -> int:
    return timezone.now().year + 5


class Movie(models.Model):
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="movies",
    )
    title = models.CharField(max_length=255)
    release_year = models.PositiveSmallIntegerField(
        blank=True,
        null=True,
        validators=[MinValueValidator(1888), MaxValueValidator(current_year)],
    )
    imdb_id = models.CharField(
        max_length=10,
        blank=True,
        null=True,
        db_index=True,
        validators=[
            RegexValidator(
                regex=r"^[0-9]{1,10}$",
                message="IMDb ID must contain digits only.",
            )
        ],
    )
    filtered_subtitle_text = models.TextField(blank=True, null=True)
    subtitle_cache_version = models.PositiveSmallIntegerField(
        blank=True,
        null=True,
        validators=[MinValueValidator(1)],
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ("-created_at", "title")
        constraints = [
            models.UniqueConstraint(
                Lower("title"),
                "user",
                "release_year",
                name="movie_user_title_year_ci_uniq",
            ),
            models.UniqueConstraint(
                Lower("title"),
                "user",
                condition=models.Q(release_year__isnull=True),
                name="movie_user_title_null_ci_uniq",
            ),
            models.CheckConstraint(
                condition=(
                    models.Q(
                        filtered_subtitle_text__isnull=True,
                        subtitle_cache_version__isnull=True,
                    )
                    | models.Q(
                        filtered_subtitle_text__isnull=False,
                        subtitle_cache_version__isnull=False,
                    )
                ),
                name="movie_subtitle_cache_state_valid",
            ),
        ]
        indexes = [
            models.Index(
                fields=("user", "-created_at"),
                name="movie_user_created_idx",
            )
        ]

    def __str__(self) -> str:
        if self.release_year:
            return f"{self.title} ({self.release_year})"
        return self.title

    @property
    def vocabulary_cefr_badge(self) -> str:
        """Summarize the mean saved-vocabulary difficulty for library cards."""
        prefetched_items = getattr(self, "_prefetched_objects_cache", {}).get(
            "vocabulary_items"
        )
        if prefetched_items is None:
            levels = self.vocabulary_items.values_list("cefr_level", flat=True)
        else:
            levels = (item.cefr_level for item in prefetched_items)

        scores = [
            CEFR_LEVEL_SCORES[level]
            for level in levels
            if level in CEFR_LEVEL_SCORES
        ]
        if not scores:
            return "—"

        average = sum(scores) / len(scores)
        nearest_score = min(
            CEFR_SCORE_LEVELS,
            key=lambda score: (abs(score - average), score),
        )
        if abs(nearest_score - average) <= 0.25:
            return f"≈{CEFR_SCORE_LEVELS[nearest_score]}"

        lower_score = int(average)
        upper_score = lower_score + 1
        return (
            f"≈{CEFR_SCORE_LEVELS[lower_score]}-"
            f"{CEFR_SCORE_LEVELS[upper_score]}"
        )
