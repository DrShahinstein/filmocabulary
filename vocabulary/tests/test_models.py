from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from movies.models import Movie
from vocabulary.models import VocabularyItem

from .factories import vocabulary_item_fields


class VocabularyItemModelTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="learner", password="test-pass-42"
        )
        cls.movie = Movie.objects.create(
            user=cls.user, title="Zodiac", release_year=2007
        )

    def test_valid_item_passes_model_validation(self):
        item = VocabularyItem(**vocabulary_item_fields(movie=self.movie))

        item.full_clean()

        self.assertEqual(item.blank_sentence, "The reporter chose to ___ every small detail.")

    def test_b1_item_passes_model_and_database_validation(self):
        fields = vocabulary_item_fields(movie=self.movie, word="abandon")
        fields["cefr_level"] = VocabularyItem.CefrLevel.B1
        item = VocabularyItem(**fields)

        item.full_clean()
        item.save()

        self.assertEqual(item.cefr_level, "B1")

    def test_item_without_cloze_data_passes_validation(self):
        fields = vocabulary_item_fields(movie=self.movie)
        fields["blank_sentence"] = None
        item = VocabularyItem(**fields)

        item.full_clean()
        item.save()

        self.assertIsNone(item.blank_sentence)
        self.assertFalse(item.is_cloze_eligible)

    def test_model_validation_rejects_incorrect_blank(self):
        fields = vocabulary_item_fields(movie=self.movie)
        fields["blank_sentence"] = "This is not the source sentence: ___."
        item = VocabularyItem(**fields)

        with self.assertRaises(ValidationError) as context:
            item.full_clean()

        self.assertIn("blank_sentence", context.exception.message_dict)

    def test_database_enforces_case_insensitive_movie_term_uniqueness(self):
        VocabularyItem.objects.create(**vocabulary_item_fields(movie=self.movie))
        duplicate = vocabulary_item_fields(movie=self.movie, word="SCRUTINIZE")
        duplicate["example_sentence"] = (
            "The reporter chose to SCRUTINIZE every small detail."
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            VocabularyItem.objects.create(**duplicate)
