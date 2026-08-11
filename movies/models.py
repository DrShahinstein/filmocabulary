from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models
from django.db.models.functions import Lower
from django.utils import timezone


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
