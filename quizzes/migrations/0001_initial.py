# Generated for Django 5.x.
from django.conf import settings
from django.db import migrations, models
import django.core.validators
import django.db.models.deletion


class Migration(migrations.Migration):
    initial = True

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("movies", "0001_initial"),
        ("vocabulary", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="QuizSession",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                (
                    "total_questions",
                    models.PositiveSmallIntegerField(
                        validators=[
                            django.core.validators.MinValueValidator(1),
                            django.core.validators.MaxValueValidator(25),
                        ]
                    ),
                ),
                ("correct_answers", models.PositiveSmallIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("completed_at", models.DateTimeField(blank=True, null=True)),
                (
                    "questions",
                    models.ManyToManyField(
                        related_name="quiz_sessions",
                        to="vocabulary.vocabularyitem",
                    ),
                ),
                (
                    "selected_movies",
                    models.ManyToManyField(
                        related_name="quiz_sessions",
                        to="movies.movie",
                    ),
                ),
                (
                    "user",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="quiz_sessions",
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                "ordering": ("-created_at",),
                "indexes": [
                    models.Index(
                        fields=["user", "-created_at"],
                        name="quiz_user_created_idx",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="QuizAttempt",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("submitted_answer", models.CharField(max_length=255)),
                ("is_correct", models.BooleanField()),
                ("answered_at", models.DateTimeField(auto_now_add=True)),
                (
                    "session",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="attempts",
                        to="quizzes.quizsession",
                    ),
                ),
                (
                    "vocabulary_item",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="quiz_attempts",
                        to="vocabulary.vocabularyitem",
                    ),
                ),
            ],
            options={"ordering": ("answered_at",)},
        ),
        migrations.AddConstraint(
            model_name="quizsession",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("total_questions__gte", 1),
                    ("total_questions__lte", 25),
                ),
                name="quiz_session_question_count_range",
            ),
        ),
        migrations.AddConstraint(
            model_name="quizsession",
            constraint=models.CheckConstraint(
                condition=models.Q(
                    ("correct_answers__lte", models.F("total_questions"))
                ),
                name="quiz_session_correct_not_above_total",
            ),
        ),
        migrations.AddConstraint(
            model_name="quizattempt",
            constraint=models.UniqueConstraint(
                fields=("session", "vocabulary_item"),
                name="unique_quiz_attempt_per_item",
            ),
        ),
    ]
