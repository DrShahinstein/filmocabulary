import warnings
from unittest.mock import call, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from movies.models import Movie
from quizzes.models import QuizAttempt, QuizSession
from vocabulary.ingestion import SourceDocument
from vocabulary.models import VocabularyItem
from vocabulary.services import (
    VocabularyGenerationResult,
    VocabularyProviderError,
)
from vocabulary.source_acquisition import AcquiredSource
from vocabulary.source_acquisition import SourceNotFoundError
from vocabulary.source_cache import CURRENT_SUBTITLE_CACHE_VERSION

from .factories import vocabulary_item_fields


@override_settings(
    RATELIMIT_ENABLE=False,
    VOCABULARY_AUTO_SOURCE_PROVIDER="",
)
class VocabularyViewTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="learner", password="test-pass-42"
        )
        cls.other_user = get_user_model().objects.create_user(
            username="other", password="test-pass-42"
        )
        cls.movie = Movie.objects.create(
            user=cls.user, title="Zodiac", release_year=2007
        )
        cls.item = VocabularyItem.objects.create(
            **vocabulary_item_fields(movie=cls.movie)
        )

    def setUp(self):
        self.client.force_login(self.user)

    @patch("vocabulary.views.generate_and_save_vocabulary")
    def test_generation_uses_validated_new_movie_fields(self, generate):
        generate.return_value = VocabularyGenerationResult(
            movie=self.movie,
            created_count=1,
            skipped_count=0,
            movie_created=False,
        )

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            response = self.client.post(
                reverse("vocabulary:generate"),
                {"title": "  Arrival ", "release_year": 2016, "item_count": 100},
                HTTP_HX_REQUEST="true",
            )

        self.assertEqual(response.status_code, 200)
        generate.assert_called_once_with(
            user=self.user,
            title="Arrival",
            release_year=2016,
            item_count=100,
            source=None,
        )
        self.assertEqual(response.headers["HX-Trigger"], "vocabularyChanged")
        self.assertFalse(
            any("{% csrf_token %}" in str(warning.message) for warning in caught)
        )

    @patch("vocabulary.views.generate_and_save_vocabulary")
    def test_generation_forwards_uploaded_srt_as_source_document(self, generate):
        generate.return_value = VocabularyGenerationResult(
            movie=self.movie,
            created_count=1,
            skipped_count=0,
            movie_created=False,
        )
        source_file = SimpleUploadedFile(
            "zodiac.srt",
            (
                b"1\r\n"
                b"00:00:01,000 --> 00:00:04,000\r\n"
                b"We must scrutinize every detail.\r\n"
            ),
            content_type="application/x-subrip",
        )

        response = self.client.post(
            reverse("vocabulary:generate"),
            {
                "title": "Zodiac",
                "release_year": 2007,
                "item_count": 10,
                "source_file": source_file,
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        generate.assert_called_once_with(
            user=self.user,
            title="Zodiac",
            release_year=2007,
            item_count=10,
            source=SourceDocument(
                text="We must scrutinize every detail.",
                format="srt",
                filename="zodiac.srt",
                pre_filtered=True,
            ),
        )

    @override_settings(VOCABULARY_AUTO_SOURCE_PROVIDER="opensubtitles")
    @patch("vocabulary.services.acquire_automatic_source")
    @patch("vocabulary.views.generate_and_save_vocabulary")
    def test_generation_uses_owned_cached_source_without_acquisition(
        self,
        generate,
        acquire_source,
    ):
        self.movie.imdb_id = "443706"
        self.movie.filtered_subtitle_text = "We must scrutinize every detail."
        self.movie.subtitle_cache_version = CURRENT_SUBTITLE_CACHE_VERSION
        self.movie.save(
            update_fields=(
                "imdb_id",
                "filtered_subtitle_text",
                "subtitle_cache_version",
            )
        )
        cached_source = SourceDocument(
            text="We must scrutinize every detail.",
            format="script",
            pre_filtered=True,
        )
        generate.return_value = VocabularyGenerationResult(
            movie=self.movie,
            created_count=1,
            skipped_count=0,
            movie_created=False,
        )

        response = self.client.post(
            reverse("vocabulary:generate"),
            {"title": "Zodiac", "release_year": 2007, "item_count": 10},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        acquire_source.assert_not_called()
        generate.assert_called_once_with(
            movie=self.movie,
            item_count=10,
            source=cached_source,
        )
        self.assertContains(response, "locally cached, pre-filtered English subtitles")

    @override_settings(VOCABULARY_AUTO_SOURCE_PROVIDER="opensubtitles")
    @patch("vocabulary.services.acquire_automatic_source")
    @patch("vocabulary.views.generate_and_save_vocabulary")
    def test_generation_makes_automatic_source_fallback_visible(
        self,
        generate,
        acquire_source,
    ):
        acquire_source.side_effect = SourceNotFoundError(
            "No matching English subtitles were found automatically."
        )
        generate.return_value = VocabularyGenerationResult(
            movie=self.movie,
            created_count=1,
            skipped_count=0,
            movie_created=False,
        )

        response = self.client.post(
            reverse("vocabulary:generate"),
            {"title": "Zodiac", "release_year": 2007, "item_count": 10},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        acquire_source.assert_called_once_with(
            title="Zodiac",
            release_year=2007,
            imdb_id=None,
        )
        generate.assert_called_once_with(
            movie=self.movie,
            item_count=10,
            source=None,
        )
        self.assertContains(response, "Generated from model knowledge instead")

    @override_settings(VOCABULARY_AUTO_SOURCE_PROVIDER="opensubtitles")
    @patch("vocabulary.services._filter_source")
    @patch("vocabulary.services.acquire_automatic_source")
    @patch("vocabulary.views.generate_and_save_vocabulary")
    def test_successful_automatic_cache_remains_when_generation_fails(
        self,
        generate,
        acquire_source,
        filter_source,
    ):
        raw_source = SourceDocument(
            text="Raw subtitle dialogue.",
            format="srt",
            filename="arrival.en.srt",
        )
        filtered_source = SourceDocument(
            text="Her hypothesis was difficult to substantiate.",
            format="srt",
            filename="arrival.en.srt",
        )
        acquire_source.return_value = AcquiredSource(
            document=raw_source,
            provider="OpenSubtitles",
            source_id="456",
            imdb_id="2543164",
        )
        bounded_source = SourceDocument(
            text=filtered_source.text,
            format="script",
            pre_filtered=True,
        )
        filter_source.side_effect = [filtered_source, bounded_source]
        generate.side_effect = VocabularyProviderError(
            "Vocabulary generation is temporarily unavailable."
        )

        response = self.client.post(
            reverse("vocabulary:generate"),
            {"title": "Arrival", "release_year": 2016, "item_count": 10},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 503)
        cached_movie = Movie.objects.get(
            user=self.user,
            title="Arrival",
            release_year=2016,
        )
        self.assertEqual(cached_movie.imdb_id, "2543164")
        self.assertEqual(
            cached_movie.filtered_subtitle_text,
            filtered_source.text,
        )
        acquire_source.assert_called_once_with(
            title="Arrival",
            release_year=2016,
            imdb_id=None,
        )
        self.assertEqual(
            filter_source.call_args_list,
            [
                call(raw_source, item_count=100),
                call(
                    SourceDocument(
                        text=filtered_source.text,
                        format="script",
                        pre_filtered=True,
                    ),
                    item_count=10,
                ),
            ],
        )
        generate.assert_called_once_with(
            movie=cached_movie,
            item_count=10,
            source=SourceDocument(
                text=filtered_source.text,
                format="script",
                pre_filtered=True,
            ),
        )

    @patch("vocabulary.views.generate_and_save_vocabulary")
    def test_invalid_source_upload_returns_422_without_generation(self, generate):
        movie_count = Movie.objects.count()
        item_count = VocabularyItem.objects.count()
        source_file = SimpleUploadedFile(
            "zodiac.pdf",
            b"not a supported source",
            content_type="application/pdf",
        )

        response = self.client.post(
            reverse("vocabulary:generate"),
            {
                "title": "Zodiac",
                "release_year": 2007,
                "item_count": 10,
                "source_file": source_file,
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 422)
        self.assertContains(
            response,
            "Upload a .txt script or .srt subtitle file.",
            status_code=422,
        )
        generate.assert_not_called()
        self.assertEqual(Movie.objects.count(), movie_count)
        self.assertEqual(VocabularyItem.objects.count(), item_count)

    @patch("vocabulary.views.generate_and_save_vocabulary")
    def test_generation_rejects_item_count_above_maximum(self, generate):
        response = self.client.post(
            reverse("vocabulary:generate"),
            {"title": "Zodiac", "release_year": 2007, "item_count": 101},
        )

        self.assertEqual(response.status_code, 422)
        self.assertContains(response, "less than or equal to 100", status_code=422)
        generate.assert_not_called()

    @patch("vocabulary.views.generate_and_save_vocabulary")
    def test_invalid_generation_form_does_not_call_provider(self, generate):
        response = self.client.post(
            reverse("vocabulary:generate"),
            {"title": "", "release_year": 2007, "item_count": 10},
        )

        self.assertEqual(response.status_code, 422)
        self.assertContains(response, "Movie title", status_code=422)
        generate.assert_not_called()

    @patch("vocabulary.views.generate_and_save_vocabulary")
    def test_provider_failure_returns_graceful_partial(self, generate):
        generate.side_effect = VocabularyProviderError(
            "Vocabulary generation is temporarily unavailable."
        )

        response = self.client.post(
            reverse("vocabulary:generate"),
            {"title": "Zodiac", "release_year": 2007, "item_count": 10},
        )

        self.assertEqual(response.status_code, 503)
        self.assertContains(response, "temporarily unavailable", status_code=503)

    def test_movie_detail_is_scoped_to_owner(self):
        response = self.client.get(
            reverse("vocabulary:movie_detail", args=[self.movie.pk])
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "scrutinize")
        self.assertTemplateUsed(response, "vocabulary/movie_detail.html")

        htmx_response = self.client.get(
            reverse("vocabulary:movie_detail", args=[self.movie.pk]),
            HTTP_HX_REQUEST="true",
        )
        self.assertTemplateUsed(
            htmx_response, "partials/vocabulary_movie_items.html"
        )
        self.assertTemplateNotUsed(htmx_response, "vocabulary/movie_detail.html")

        self.client.force_login(self.other_user)
        response = self.client.get(
            reverse("vocabulary:movie_detail", args=[self.movie.pk])
        )
        self.assertEqual(response.status_code, 404)

    def test_delete_is_post_only_and_scoped_to_owner(self):
        url = reverse("vocabulary:item_delete", args=[self.item.pk])
        self.assertEqual(self.client.get(url).status_code, 405)

        self.client.force_login(self.other_user)
        self.assertEqual(self.client.post(url).status_code, 404)
        self.assertTrue(VocabularyItem.objects.filter(pk=self.item.pk).exists())

        self.client.force_login(self.user)
        response = self.client.post(url, HTTP_HX_REQUEST="true")
        self.assertEqual(response.status_code, 204)
        self.assertFalse(VocabularyItem.objects.filter(pk=self.item.pk).exists())

    def test_delete_removes_active_quiz_session_containing_item(self):
        session = QuizSession.objects.create(
            user=self.user,
            total_questions=1,
        )
        session.selected_movies.add(self.movie)
        session.questions.add(self.item)

        response = self.client.post(
            reverse("vocabulary:item_delete", args=[self.item.pk]),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(QuizSession.objects.filter(pk=session.pk).exists())
        self.assertFalse(VocabularyItem.objects.filter(pk=self.item.pk).exists())

    def test_delete_removes_completed_quiz_session_containing_item(self):
        session = QuizSession.objects.create(
            user=self.user,
            total_questions=1,
            correct_answers=1,
            completed_at=timezone.now(),
        )
        session.selected_movies.add(self.movie)
        session.questions.add(self.item)
        QuizAttempt.objects.create(
            session=session,
            vocabulary_item=self.item,
            submitted_answer=self.item.word_or_phrase,
            is_correct=True,
        )

        response = self.client.post(
            reverse("vocabulary:item_delete", args=[self.item.pk]),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(QuizSession.objects.filter(pk=session.pk).exists())
        self.assertFalse(QuizAttempt.objects.filter(session_id=session.pk).exists())
        self.assertFalse(VocabularyItem.objects.filter(pk=self.item.pk).exists())

    def test_generation_requires_login_and_csrf(self):
        self.client.logout()
        response = self.client.post(
            reverse("vocabulary:generate"),
            {"title": "Zodiac", "release_year": 2007, "item_count": 10},
        )
        self.assertEqual(response.status_code, 302)

        csrf_client = Client(enforce_csrf_checks=True)
        csrf_client.force_login(self.user)
        response = csrf_client.post(
            reverse("vocabulary:generate"),
            {"title": "Zodiac", "release_year": 2007, "item_count": 10},
        )
        self.assertEqual(response.status_code, 403)

    def test_generation_forms_support_multipart_htmx_uploads(self):
        for view_name in ("movies:dashboard", "vocabulary:list"):
            with self.subTest(view_name=view_name):
                response = self.client.get(reverse(view_name))

                self.assertEqual(response.status_code, 200)
                self.assertContains(response, 'enctype="multipart/form-data"')
                self.assertContains(response, 'hx-encoding="multipart/form-data"')
                self.assertContains(response, 'name="source_file"')


@override_settings(
    RATELIMIT_ENABLE=True,
    VOCABULARY_AUTO_SOURCE_PROVIDER="",
)
class VocabularyRateLimitTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = get_user_model().objects.create_user(
            username="rate-limited-learner", password="test-pass-42"
        )
        self.movie = Movie.objects.create(
            user=self.user, title="Zodiac", release_year=2007
        )
        self.client.force_login(self.user)

    @patch("vocabulary.views.generate_and_save_vocabulary")
    def test_sixth_generation_request_is_rejected(self, generate):
        generate.return_value = VocabularyGenerationResult(
            movie=self.movie,
            created_count=1,
            skipped_count=0,
            movie_created=False,
        )
        url = reverse("vocabulary:generate")
        data = {"title": "Zodiac", "release_year": 2007, "item_count": 10}

        for _ in range(5):
            self.assertEqual(self.client.post(url, data).status_code, 200)

        response = self.client.post(url, data)

        self.assertEqual(response.status_code, 429)
        self.assertContains(response, "generation limit", status_code=429)
        self.assertEqual(generate.call_count, 5)
