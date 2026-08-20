from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from movies.models import Movie
from quizzes.models import UserWordStatus
from vocabulary.models import VocabularyItem
from vocabulary.querysets import (
    VocabularyFilterSpec,
    filter_vocabulary_queryset,
    owned_vocabulary_queryset,
)

from .factories import vocabulary_item_fields


class VocabularyFilterSpecTests(SimpleTestCase):
    def test_cleaned_form_data_becomes_a_canonical_immutable_spec(self):
        spec = VocabularyFilterSpec.from_cleaned_data(
            {
                "q": "hidden meaning",
                "status": "saved",
                "type": VocabularyItem.Type.IDIOM,
                "movie": SimpleNamespace(pk=12),
                "cefr": ["C2", "B1", "C2"],
            }
        )

        self.assertEqual(
            spec,
            VocabularyFilterSpec(
                q="hidden meaning",
                status="saved",
                word_type=VocabularyItem.Type.IDIOM,
                movie_id=12,
                cefr_levels=("B1", "C2"),
            ),
        )
        with self.assertRaises(AttributeError):
            spec.status = "new"

    def test_payload_round_trip_is_json_compatible_and_strict(self):
        spec = VocabularyFilterSpec(
            q="hidden meaning",
            status="learning",
            word_type=VocabularyItem.Type.NOUN,
            movie_id=7,
            cefr_levels=("B2", "C1"),
        )

        payload = spec.as_payload()

        self.assertEqual(
            payload,
            {
                "q": "hidden meaning",
                "status": "learning",
                "word_type": "noun",
                "movie_id": 7,
                "cefr_levels": ["B2", "C1"],
            },
        )
        self.assertEqual(VocabularyFilterSpec.from_payload(payload), spec)

    def test_payload_rejects_unknown_missing_mistyped_and_noncanonical_values(self):
        valid = VocabularyFilterSpec().as_payload()
        invalid_payloads = (
            {**valid, "unexpected": "value"},
            {key: value for key, value in valid.items() if key != "status"},
            {**valid, "cefr_levels": ("B1",)},
            {**valid, "status": "archived"},
            {**valid, "movie_id": True},
            {**valid, "q": "two  spaces"},
            {**valid, "cefr_levels": ["C1", "B1"]},
            {**valid, "cefr_levels": ["B1", "B1"]},
        )

        for payload in invalid_payloads:
            with self.subTest(payload=payload), self.assertRaises(ValueError):
                VocabularyFilterSpec.from_payload(payload)

    def test_query_representation_uses_only_active_public_filter_names(self):
        spec = VocabularyFilterSpec(
            q="hidden meaning",
            status="saved",
            word_type=VocabularyItem.Type.IDIOM,
            movie_id=12,
            cefr_levels=("B1", "C2"),
        )

        self.assertEqual(
            spec.as_query_pairs(),
            (
                ("q", "hidden meaning"),
                ("status", "saved"),
                ("type", "idiom"),
                ("movie", "12"),
                ("cefr", "B1"),
                ("cefr", "C2"),
            ),
        )
        self.assertEqual(
            spec.as_query_string(),
            "q=hidden+meaning&status=saved&type=idiom&movie=12&cefr=B1&cefr=C2",
        )
        self.assertEqual(VocabularyFilterSpec().as_query_pairs(), ())
        self.assertEqual(VocabularyFilterSpec().as_query_string(), "")


class VocabularyQuerysetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="queryset-learner",
            password="test-pass-42",
        )
        cls.other_user = get_user_model().objects.create_user(
            username="queryset-other",
            password="test-pass-42",
        )
        cls.movie = Movie.objects.create(
            user=cls.user,
            title="Zodiac",
            release_year=2007,
        )
        cls.second_movie = Movie.objects.create(
            user=cls.user,
            title="Arrival",
            release_year=2016,
        )
        cls.implicit_new = VocabularyItem.objects.create(
            **vocabulary_item_fields(movie=cls.movie, word="scrutinize")
        )
        cls.explicit_new = VocabularyItem.objects.create(
            **vocabulary_item_fields(movie=cls.movie, word="contemplate")
        )
        cls.learning = VocabularyItem.objects.create(
            **vocabulary_item_fields(movie=cls.movie, word="substantiate")
        )
        cls.mastered = VocabularyItem.objects.create(
            **vocabulary_item_fields(movie=cls.movie, word="meticulous")
        )
        cls.combined_match = VocabularyItem.objects.create(
            movie=cls.second_movie,
            word_or_phrase="conundrum",
            type=VocabularyItem.Type.NOUN,
            cefr_level=VocabularyItem.CefrLevel.C2,
            definition_en="A difficult problem.",
            example_sentence="The apparent conundrum changed her approach.",
            blank_sentence="The apparent ___ changed her approach.",
        )
        cls.combined_nonmatch = VocabularyItem.objects.create(
            movie=cls.second_movie,
            word_or_phrase="elusive",
            type=VocabularyItem.Type.ADJECTIVE,
            cefr_level=VocabularyItem.CefrLevel.C1,
            definition_en="Difficult to find.",
            example_sentence="The answer remained elusive.",
            blank_sentence="The answer remained ___.",
        )
        other_movie = Movie.objects.create(
            user=cls.other_user,
            title="Heat",
            release_year=1995,
        )
        cls.outsider = VocabularyItem.objects.create(
            **vocabulary_item_fields(movie=other_movie, word="outsider")
        )

        UserWordStatus.objects.create(
            user=cls.user,
            vocabulary_item=cls.explicit_new,
            status=UserWordStatus.Status.NEW,
        )
        UserWordStatus.objects.create(
            user=cls.user,
            vocabulary_item=cls.learning,
            status=UserWordStatus.Status.LEARNING,
            is_saved=True,
        )
        UserWordStatus.objects.create(
            user=cls.user,
            vocabulary_item=cls.mastered,
            status=UserWordStatus.Status.MASTERED,
        )

    def test_owned_queryset_scopes_rows_and_exposes_current_annotations(self):
        items = {
            item.pk: item for item in owned_vocabulary_queryset(self.user)
        }

        self.assertNotIn(self.outsider.pk, items)
        self.assertEqual(items[self.implicit_new.pk].learning_status, "new")
        self.assertEqual(items[self.explicit_new.pk].learning_status, "new")
        self.assertEqual(items[self.learning.pk].learning_status, "learning")
        self.assertTrue(items[self.learning.pk].is_saved_for_user)
        self.assertEqual(items[self.mastered.pk].learning_status, "mastered")
        self.assertFalse(items[self.mastered.pk].is_saved_for_user)
        with self.assertNumQueries(0):
            self.assertEqual(items[self.implicit_new.pk].movie.title, "Zodiac")

    def test_status_filters_preserve_implicit_new_and_saved_semantics(self):
        expected_ids = {
            "new": {
                self.implicit_new.pk,
                self.explicit_new.pk,
                self.combined_match.pk,
                self.combined_nonmatch.pk,
            },
            "learning": {self.learning.pk},
            "mastered": {self.mastered.pk},
            "saved": {self.learning.pk},
        }

        for status, expected in expected_ids.items():
            with self.subTest(status=status):
                queryset = filter_vocabulary_queryset(
                    owned_vocabulary_queryset(self.user),
                    VocabularyFilterSpec(status=status),
                )
                self.assertSetEqual(
                    set(queryset.values_list("pk", flat=True)),
                    expected,
                )

    def test_search_matches_term_definition_and_example_sentence(self):
        for query in ("conundrum", "difficult problem", "changed her approach"):
            with self.subTest(query=query):
                queryset = filter_vocabulary_queryset(
                    owned_vocabulary_queryset(self.user),
                    VocabularyFilterSpec(q=query),
                )
                self.assertSetEqual(
                    set(queryset.values_list("pk", flat=True)),
                    {self.combined_match.pk},
                )

    def test_type_movie_and_cefr_filters_compose(self):
        queryset = filter_vocabulary_queryset(
            owned_vocabulary_queryset(self.user),
            VocabularyFilterSpec(
                word_type=VocabularyItem.Type.NOUN,
                movie_id=self.second_movie.pk,
                cefr_levels=("C1", "C2"),
            ),
        )

        self.assertQuerySetEqual(queryset, [self.combined_match], ordered=False)
