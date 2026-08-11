from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings

from movies.models import Movie
from vocabulary.ingestion import SourceDocument
from vocabulary.services import prepare_vocabulary_source
from vocabulary.source_acquisition import AcquiredSource, SourceNotFoundError
from vocabulary.source_cache import CURRENT_SUBTITLE_CACHE_VERSION


@override_settings(VOCABULARY_AUTO_SOURCE_PROVIDER="opensubtitles")
class PreparedVocabularySourceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="source-owner")
        cls.other_user = get_user_model().objects.create_user(
            username="other-source-owner"
        )

    @staticmethod
    def acquired_source(text="We must scrutinize every detail."):
        return AcquiredSource(
            document=SourceDocument(
                text=text,
                format="srt",
                filename="zodiac.en.srt",
            ),
            provider="OpenSubtitles",
            source_id="123",
            imdb_id="443706",
        )

    @patch("vocabulary.services.acquire_automatic_source")
    def test_uploaded_source_is_filtered_without_automatic_acquisition(
        self,
        acquire_source,
    ):
        uploaded = SourceDocument(
            text="I am here.\nWe must scrutinize every detail.",
            format="srt",
            filename="zodiac.srt",
        )

        prepared = prepare_vocabulary_source(
            user=self.user,
            title="Zodiac",
            release_year=2007,
            uploaded_source=uploaded,
        )

        acquire_source.assert_not_called()
        self.assertEqual(
            prepared.source,
            SourceDocument(
                text="We must scrutinize every detail.",
                format="srt",
                filename="zodiac.srt",
                pre_filtered=True,
            ),
        )
        self.assertIsNone(prepared.movie)
        self.assertFalse(prepared.cache_hit)

    @patch("vocabulary.services.acquire_automatic_source")
    def test_current_owned_cache_hit_skips_automatic_acquisition(
        self,
        acquire_source,
    ):
        movie = Movie.objects.create(
            user=self.user,
            title="Zodiac",
            release_year=2007,
            imdb_id="443706",
            filtered_subtitle_text="We must scrutinize every detail.",
            subtitle_cache_version=CURRENT_SUBTITLE_CACHE_VERSION,
        )

        prepared = prepare_vocabulary_source(
            user=self.user,
            title="zodiac",
            release_year=2007,
        )

        acquire_source.assert_not_called()
        self.assertTrue(prepared.cache_hit)
        self.assertEqual(prepared.movie, movie)
        self.assertEqual(
            prepared.source,
            SourceDocument(
                text="We must scrutinize every detail.",
                format="script",
                pre_filtered=True,
            ),
        )

    @patch("vocabulary.services._filter_source")
    @patch("vocabulary.services.acquire_automatic_source")
    def test_cache_miss_uses_saved_imdb_id_then_filters_and_stores(
        self,
        acquire_source,
        filter_source,
    ):
        movie = Movie.objects.create(
            user=self.user,
            title="Zodiac",
            release_year=2007,
            imdb_id="443706",
        )
        acquired = self.acquired_source(text="Raw subtitle dialogue.")
        filtered = SourceDocument(
            text="We must scrutinize every detail.",
            format="srt",
            filename="zodiac.en.srt",
        )
        acquire_source.return_value = acquired
        filter_source.return_value = filtered

        prepared = prepare_vocabulary_source(
            user=self.user,
            title="Zodiac",
            release_year=2007,
        )

        acquire_source.assert_called_once_with(
            title="Zodiac",
            release_year=2007,
            imdb_id="443706",
        )
        filter_source.assert_called_once_with(acquired.document)
        movie.refresh_from_db()
        self.assertEqual(
            movie.filtered_subtitle_text,
            "We must scrutinize every detail.",
        )
        self.assertEqual(
            movie.subtitle_cache_version,
            CURRENT_SUBTITLE_CACHE_VERSION,
        )
        self.assertEqual(prepared.movie, movie)
        self.assertEqual(prepared.source.text, movie.filtered_subtitle_text)

    @patch("vocabulary.services._filter_source")
    @patch("vocabulary.services.acquire_automatic_source")
    def test_another_users_cache_is_not_reused(
        self,
        acquire_source,
        filter_source,
    ):
        other_movie = Movie.objects.create(
            user=self.other_user,
            title="Zodiac",
            release_year=2007,
            imdb_id="443706",
            filtered_subtitle_text="Another user's private cache.",
            subtitle_cache_version=CURRENT_SUBTITLE_CACHE_VERSION,
        )
        acquired = self.acquired_source()
        acquire_source.return_value = acquired
        filter_source.return_value = acquired.document

        prepared = prepare_vocabulary_source(
            user=self.user,
            title="Zodiac",
            release_year=2007,
        )

        acquire_source.assert_called_once_with(
            title="Zodiac",
            release_year=2007,
            imdb_id=None,
        )
        self.assertEqual(prepared.movie.user, self.user)
        self.assertNotEqual(prepared.movie, other_movie)
        self.assertEqual(Movie.objects.filter(title="Zodiac").count(), 2)
        other_movie.refresh_from_db()
        self.assertEqual(
            other_movie.filtered_subtitle_text,
            "Another user's private cache.",
        )

    @patch("vocabulary.services.acquire_automatic_source")
    def test_failed_acquisition_falls_back_without_writing_negative_cache(
        self,
        acquire_source,
    ):
        acquire_source.side_effect = SourceNotFoundError(
            "No matching English subtitles were found automatically."
        )

        prepared = prepare_vocabulary_source(
            user=self.user,
            title="Zodiac",
            release_year=2007,
        )

        self.assertIsNone(prepared.source)
        self.assertIsNone(prepared.movie)
        self.assertIn("Generated from model knowledge instead", prepared.note)
        self.assertFalse(Movie.objects.filter(user=self.user).exists())

    @patch("vocabulary.services._filter_source")
    @patch("vocabulary.services.acquire_automatic_source")
    def test_empty_filter_result_is_negative_cached_and_skips_next_fetch(
        self,
        acquire_source,
        filter_source,
    ):
        acquired = self.acquired_source(text="I am here.")
        acquire_source.return_value = acquired
        filter_source.return_value = SourceDocument(
            text="",
            format="srt",
            filename="zodiac.en.srt",
        )

        first = prepare_vocabulary_source(
            user=self.user,
            title="Zodiac",
            release_year=2007,
        )
        second = prepare_vocabulary_source(
            user=self.user,
            title="Zodiac",
            release_year=2007,
        )

        acquire_source.assert_called_once_with(
            title="Zodiac",
            release_year=2007,
            imdb_id=None,
        )
        self.assertIsNone(first.source)
        self.assertIsNone(second.source)
        self.assertTrue(second.cache_hit)
        movie = Movie.objects.get(user=self.user, title="Zodiac")
        self.assertEqual(movie.filtered_subtitle_text, "")
        self.assertEqual(
            movie.subtitle_cache_version,
            CURRENT_SUBTITLE_CACHE_VERSION,
        )
