from django.core.exceptions import ValidationError
from django.db import models
from django.db.models.functions import Lower

from .text import BlankSentenceError, validate_blank_sentence


class VocabularyItem(models.Model):
    class Type(models.TextChoices):
        PHRASAL_VERB = "phrasal_verb", "Phrasal verb"
        IDIOM = "idiom", "Idiom"
        COLLOCATION = "collocation", "Collocation"
        NOUN = "noun", "Noun"
        ADJECTIVE = "adjective", "Adjective"
        VERB = "verb", "Verb"
        ADVERB = "adverb", "Adverb"
        OTHER = "other", "Other"

    class CefrLevel(models.TextChoices):
        B2 = "B2", "B2"
        C1 = "C1", "C1"
        C2 = "C2", "C2"

    movie = models.ForeignKey(
        "movies.Movie",
        on_delete=models.CASCADE,
        related_name="vocabulary_items",
    )
    word_or_phrase = models.CharField(max_length=255)
    type = models.CharField(max_length=32, choices=Type.choices)
    cefr_level = models.CharField(max_length=2, choices=CefrLevel.choices)
    definition_en = models.TextField()
    example_sentence = models.TextField()
    blank_sentence = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ("cefr_level", "word_or_phrase", "pk")
        constraints = (
            models.UniqueConstraint(
                Lower("word_or_phrase"),
                "movie",
                name="vocab_unique_movie_term_ci",
            ),
            models.CheckConstraint(
                condition=models.Q(cefr_level__in=("B2", "C1", "C2")),
                name="vocab_valid_cefr",
            ),
            models.CheckConstraint(
                condition=models.Q(
                    type__in=(
                        "phrasal_verb",
                        "idiom",
                        "collocation",
                        "noun",
                        "adjective",
                        "verb",
                        "adverb",
                        "other",
                    )
                ),
                name="vocab_valid_type",
            ),
        )
        indexes = (
            models.Index(
                fields=("movie", "cefr_level"),
                name="vocab_movie_cefr_idx",
            ),
            models.Index(
                fields=("movie", "type"),
                name="vocab_movie_type_idx",
            ),
        )

    def __str__(self) -> str:
        return f"{self.word_or_phrase} ({self.movie})"

    def clean(self) -> None:
        super().clean()
        try:
            self.blank_sentence = validate_blank_sentence(
                self.word_or_phrase,
                self.example_sentence,
                self.blank_sentence,
            )
        except BlankSentenceError as exc:
            raise ValidationError({"blank_sentence": str(exc)}) from exc
