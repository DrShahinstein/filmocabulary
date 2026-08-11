import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [("movies", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="movie",
            name="filtered_subtitle_text",
            field=models.TextField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="movie",
            name="imdb_id",
            field=models.CharField(
                blank=True,
                db_index=True,
                max_length=10,
                null=True,
                validators=[
                    django.core.validators.RegexValidator(
                        message="IMDb ID must contain digits only.",
                        regex="^[0-9]{1,10}$",
                    )
                ],
            ),
        ),
        migrations.AddField(
            model_name="movie",
            name="subtitle_cache_version",
            field=models.PositiveSmallIntegerField(
                blank=True,
                null=True,
                validators=[django.core.validators.MinValueValidator(1)],
            ),
        ),
        migrations.AddConstraint(
            model_name="movie",
            constraint=models.CheckConstraint(
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
        ),
    ]
