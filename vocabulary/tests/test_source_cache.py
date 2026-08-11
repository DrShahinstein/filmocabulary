from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase

from movies.models import Movie
from vocabulary.source_cache import (
    CURRENT_SUBTITLE_CACHE_VERSION,
    SubtitleCacheConflictError,
    SubtitleCacheError,
    build_source_document_from_cache,
    lookup_owned_subtitle_cache,
    normalise_imdb_id,
    store_owned_subtitle_cache,
)


class SubtitleCacheTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="cache-owner")
        cls.other_user = get_user_model().objects.create_user(username="other-owner")

    def test_null_is_a_miss_and_empty_text_is_a_negative_hit(self):
        movie = Movie.objects.create(
            user=self.user,
            title="Zodiac",
            release_year=2007,
        )

        missing = lookup_owned_subtitle_cache(
            user=self.user,
            title="Zodiac",
            release_year=2007,
        )

        self.assertEqual(missing.movie, movie)
        self.assertFalse(missing.cache_hit)
        self.assertFalse(missing.negative_hit)

        negative = store_owned_subtitle_cache(
            user=self.user,
            title="Zodiac",
            release_year=2007,
            imdb_id=None,
            filtered_text="   ",
        )

        self.assertTrue(negative.cache_written)
        self.assertTrue(negative.cache_hit)
        self.assertTrue(negative.negative_hit)
        self.assertIsNone(negative.document)

    def test_lookup_is_scoped_to_the_authenticated_owner(self):
        Movie.objects.create(
            user=self.other_user,
            title="Zodiac",
            release_year=2007,
            filtered_subtitle_text="We must scrutinize every clue.",
            subtitle_cache_version=CURRENT_SUBTITLE_CACHE_VERSION,
        )

        result = lookup_owned_subtitle_cache(
            user=self.user,
            title="Zodiac",
            release_year=2007,
        )

        self.assertIsNone(result.movie)
        self.assertFalse(result.cache_hit)

    def test_current_cache_builds_a_plain_text_source_document(self):
        movie = Movie.objects.create(
            user=self.user,
            title="Zodiac",
            release_year=2007,
            imdb_id="443706",
            filtered_subtitle_text="We must scrutinize every clue.",
            subtitle_cache_version=CURRENT_SUBTITLE_CACHE_VERSION,
        )

        document = build_source_document_from_cache(movie)

        self.assertIsNotNone(document)
        self.assertEqual(document.text, "We must scrutinize every clue.")
        self.assertEqual(document.format, "script")
        self.assertIsNone(document.filename)

    def test_stale_version_is_a_miss_and_can_be_replaced(self):
        old_version = CURRENT_SUBTITLE_CACHE_VERSION
        new_version = old_version + 1
        movie = Movie.objects.create(
            user=self.user,
            title="Zodiac",
            release_year=2007,
            imdb_id="443706",
            filtered_subtitle_text="Old filtered dialogue.",
            subtitle_cache_version=old_version,
        )

        stale = lookup_owned_subtitle_cache(
            user=self.user,
            title="Zodiac",
            release_year=2007,
            cache_version=new_version,
        )
        stored = store_owned_subtitle_cache(
            user=self.user,
            title="Zodiac",
            release_year=2007,
            imdb_id="tt0443706",
            filtered_text="New filtered dialogue.",
            cache_version=new_version,
        )

        self.assertTrue(stale.stale)
        self.assertFalse(stale.cache_hit)
        self.assertTrue(stored.cache_hit)
        self.assertTrue(stored.cache_written)
        movie.refresh_from_db()
        self.assertEqual(movie.filtered_subtitle_text, "New filtered dialogue.")
        self.assertEqual(movie.subtitle_cache_version, new_version)

    def test_same_version_cache_is_first_writer_wins(self):
        first = store_owned_subtitle_cache(
            user=self.user,
            title="  Zodiac  ",
            release_year=2007,
            imdb_id="tt0443706",
            filtered_text="First filtered dialogue.",
        )
        second = store_owned_subtitle_cache(
            user=self.user,
            title="ZODIAC",
            release_year=2007,
            imdb_id=443706,
            filtered_text="Second filtered dialogue.",
        )

        self.assertTrue(first.movie_created)
        self.assertTrue(first.cache_written)
        self.assertFalse(second.movie_created)
        self.assertFalse(second.cache_written)
        self.assertEqual(second.document.text, "First filtered dialogue.")
        self.assertEqual(Movie.objects.count(), 1)

    def test_store_normalises_imdb_id_and_rejects_a_conflict(self):
        stored = store_owned_subtitle_cache(
            user=self.user,
            title="Zodiac",
            release_year=2007,
            imdb_id="tt0443706",
            filtered_text="Filtered dialogue.",
        )

        self.assertEqual(stored.movie.imdb_id, "443706")
        with self.assertRaises(SubtitleCacheConflictError):
            store_owned_subtitle_cache(
                user=self.user,
                title="Zodiac",
                release_year=2007,
                imdb_id="1375666",
                filtered_text="Conflicting dialogue.",
            )
        stored.movie.refresh_from_db()
        self.assertEqual(stored.movie.imdb_id, "443706")
        self.assertEqual(stored.movie.filtered_subtitle_text, "Filtered dialogue.")

    def test_database_requires_text_and_version_to_share_cache_state(self):
        with self.assertRaises(IntegrityError), transaction.atomic():
            Movie.objects.create(
                user=self.user,
                title="Zodiac",
                release_year=2007,
                filtered_subtitle_text="Filtered dialogue.",
            )

        with self.assertRaises(IntegrityError), transaction.atomic():
            Movie.objects.create(
                user=self.user,
                title="Arrival",
                release_year=2016,
                subtitle_cache_version=CURRENT_SUBTITLE_CACHE_VERSION,
            )

    def test_rejects_invalid_identity_and_version_inputs(self):
        for imdb_id in (True, "tt-nope", 0, ""):
            with self.subTest(imdb_id=imdb_id), self.assertRaises(
                SubtitleCacheError
            ):
                normalise_imdb_id(imdb_id)

        with self.assertRaises(SubtitleCacheError):
            lookup_owned_subtitle_cache(
                user=self.user,
                title="Zodiac",
                release_year=2007,
                cache_version=0,
            )
