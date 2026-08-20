import warnings
from unittest.mock import call, patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from movies.models import Movie
from quizzes.models import UserWordStatus
from vocabulary.ingestion import SourceDocument
from vocabulary.models import VocabularyItem
from vocabulary.querysets import VocabularyFilterSpec
from vocabulary.services import (
    CandidateRejections,
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
    def test_generation_reports_provider_shortfall_without_guessing_cause(
        self, generate
    ):
        generate.return_value = VocabularyGenerationResult(
            movie=self.movie,
            created_count=77,
            skipped_count=0,
            movie_created=False,
            requested_count=80,
            provider_returned_count=77,
            validated_candidate_count=77,
        )

        response = self.client.post(
            reverse("vocabulary:generate"),
            {"title": "Zodiac", "release_year": 2007, "item_count": 80},
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(response, "Saved 77 of 80 requested new entries.")
        self.assertContains(
            response,
            "This generation run returned fewer candidates than requested.",
        )
        self.assertNotContains(response, "script was too short")

    @patch("vocabulary.views.generate_and_save_vocabulary")
    def test_generation_reports_filtered_candidates_as_shortfall_reason(
        self, generate
    ):
        generate.return_value = VocabularyGenerationResult(
            movie=self.movie,
            created_count=77,
            skipped_count=0,
            movie_created=False,
            requested_count=80,
            provider_returned_count=80,
            validated_candidate_count=77,
            candidate_rejections=CandidateRejections(ungrounded=3),
        )

        response = self.client.post(
            reverse("vocabulary:generate"),
            {"title": "Zodiac", "release_year": 2007, "item_count": 80},
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(response, "Saved 77 of 80 requested new entries.")
        self.assertContains(
            response,
            "3 generated candidates did not match the supplied source and were excluded.",
        )
        self.assertNotContains(response, "provider returned fewer candidates")

    @patch("vocabulary.views.generate_and_save_vocabulary")
    def test_generation_reports_malformed_candidates_as_shortfall_reason(
        self, generate
    ):
        generate.return_value = VocabularyGenerationResult(
            movie=self.movie,
            created_count=77,
            skipped_count=0,
            movie_created=False,
            requested_count=80,
            provider_returned_count=80,
            validated_candidate_count=77,
            candidate_rejections=CandidateRejections(malformed=3),
        )

        response = self.client.post(
            reverse("vocabulary:generate"),
            {"title": "Zodiac", "release_year": 2007, "item_count": 80},
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(
            response,
            "3 generated candidates had invalid structured data and were excluded.",
        )

    @patch("vocabulary.views.generate_and_save_vocabulary")
    def test_generation_attributes_shortfall_to_already_saved_entries(
        self, generate
    ):
        generate.return_value = VocabularyGenerationResult(
            movie=self.movie,
            created_count=77,
            skipped_count=3,
            movie_created=False,
            requested_count=80,
            provider_returned_count=95,
            validated_candidate_count=80,
        )

        response = self.client.post(
            reverse("vocabulary:generate"),
            {"title": "Zodiac", "release_year": 2007, "item_count": 80},
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(
            response,
            "3 qualifying entries were already saved and were not duplicated.",
        )
        self.assertNotContains(response, "valid quiz example")

    @patch("vocabulary.views.generate_and_save_vocabulary")
    def test_generation_keeps_standard_message_for_full_yield(self, generate):
        generate.return_value = VocabularyGenerationResult(
            movie=self.movie,
            created_count=80,
            skipped_count=0,
            movie_created=False,
            requested_count=80,
            provider_returned_count=80,
            validated_candidate_count=80,
        )

        response = self.client.post(
            reverse("vocabulary:generate"),
            {"title": "Zodiac", "release_year": 2007, "item_count": 80},
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(response, "80 new entries saved")
        self.assertNotContains(response, "of 80 requested new entries")
        self.assertContains(
            response,
            'class="message message--success message--generation"',
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

    def test_words_explorer_requires_login_and_scopes_items_to_owner(self):
        other_movie = Movie.objects.create(
            user=self.other_user,
            title="Heat",
            release_year=1995,
        )
        outsider = VocabularyItem.objects.create(
            **vocabulary_item_fields(movie=other_movie, word="outsider")
        )
        url = reverse("words:index")

        response = self.client.get(url)

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "vocabulary/words_explorer.html")
        self.assertContains(response, self.item.word_or_phrase)
        self.assertNotContains(response, outsider.word_or_phrase)
        self.assertEqual(
            response.context["vocabulary_filter_spec"],
            VocabularyFilterSpec(),
        )
        self.assertEqual(response.context["filter_query"], "")

        self.client.logout()
        response = self.client.get(url)
        self.assertRedirects(response, f"{reverse('login')}?next={url}")

    def test_words_explorer_searches_term_definition_and_context(self):
        phrase = VocabularyItem.objects.create(
            movie=self.movie,
            word_or_phrase="read between the lines",
            type=VocabularyItem.Type.IDIOM,
            cefr_level=VocabularyItem.CefrLevel.C1,
            definition_en="Infer a concealed meaning.",
            example_sentence="She could read between the lines of his denial.",
            blank_sentence="She could ___ of his denial.",
        )

        for query in ("between", "concealed", "denial"):
            with self.subTest(query=query):
                response = self.client.get(reverse("words:index"), {"q": query})
                self.assertEqual(list(response.context["items"]), [phrase])

    def test_words_explorer_status_filters_include_implicit_new_and_saved(self):
        learning_item = VocabularyItem.objects.create(
            **vocabulary_item_fields(movie=self.movie, word="contemplate")
        )
        mastered_item = VocabularyItem.objects.create(
            **vocabulary_item_fields(movie=self.movie, word="substantiate")
        )
        saved_item = VocabularyItem.objects.create(
            **vocabulary_item_fields(movie=self.movie, word="meticulous")
        )
        UserWordStatus.objects.create(
            user=self.user,
            vocabulary_item=learning_item,
            status=UserWordStatus.Status.LEARNING,
        )
        UserWordStatus.objects.create(
            user=self.user,
            vocabulary_item=mastered_item,
            status=UserWordStatus.Status.MASTERED,
        )
        UserWordStatus.objects.create(
            user=self.user,
            vocabulary_item=saved_item,
            status=UserWordStatus.Status.LEARNING,
            is_saved=True,
        )

        new_response = self.client.get(reverse("words:index"), {"status": "new"})
        saved_response = self.client.get(
            reverse("words:index"), {"status": "saved"}
        )

        self.assertEqual(list(new_response.context["items"]), [self.item])
        self.assertEqual(list(saved_response.context["items"]), [saved_item])
        self.assertEqual(saved_response.context["items"][0].learning_status, "learning")

    def test_words_explorer_combines_type_movie_and_cefr_filters(self):
        second_movie = Movie.objects.create(
            user=self.user,
            title="Arrival",
            release_year=2016,
        )
        matching_item = VocabularyItem.objects.create(
            movie=second_movie,
            word_or_phrase="conundrum",
            type=VocabularyItem.Type.NOUN,
            cefr_level=VocabularyItem.CefrLevel.C2,
            definition_en="A difficult problem.",
            example_sentence="The apparent conundrum changed her approach.",
            blank_sentence="The apparent ___ changed her approach.",
        )
        VocabularyItem.objects.create(
            movie=second_movie,
            word_or_phrase="elusive",
            type=VocabularyItem.Type.ADJECTIVE,
            cefr_level=VocabularyItem.CefrLevel.C1,
            definition_en="Difficult to find.",
            example_sentence="The answer remained elusive.",
            blank_sentence="The answer remained ___.",
        )

        response = self.client.get(
            reverse("words:index"),
            {
                "movie": second_movie.pk,
                "type": VocabularyItem.Type.NOUN,
                "cefr": [VocabularyItem.CefrLevel.C1, VocabularyItem.CefrLevel.C2],
            },
        )

        self.assertEqual(list(response.context["items"]), [matching_item])

    def test_words_explorer_rejects_another_users_movie_filter(self):
        other_movie = Movie.objects.create(
            user=self.other_user,
            title="Heat",
            release_year=1995,
        )
        outsider = VocabularyItem.objects.create(
            **vocabulary_item_fields(movie=other_movie, word="outsider")
        )

        response = self.client.get(
            reverse("words:index"),
            {"movie": other_movie.pk},
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(response.context["filter_form"].is_valid())
        self.assertEqual(list(response.context["items"]), [])
        self.assertIsNone(response.context["vocabulary_filter_spec"])
        self.assertEqual(response.context["filter_query"], "")
        self.assertNotContains(response, outsider.word_or_phrase)

    def test_words_explorer_exposes_canonical_validated_filter_state(self):
        response = self.client.get(
            reverse("words:index"),
            {
                "q": "  difficult   problem ",
                "status": "saved",
                "type": VocabularyItem.Type.NOUN,
                "movie": self.movie.pk,
                "cefr": ["C2", "B1", "C2"],
                "page": 1,
                "ignored": "do-not-preserve",
            },
        )

        expected = VocabularyFilterSpec(
            q="difficult problem",
            status="saved",
            word_type=VocabularyItem.Type.NOUN,
            movie_id=self.movie.pk,
            cefr_levels=("B1", "C2"),
        )
        self.assertEqual(response.context["vocabulary_filter_spec"], expected)
        self.assertEqual(
            response.context["filter_query"],
            (
                "q=difficult+problem&status=saved&type=noun"
                f"&movie={self.movie.pk}&cefr=B1&cefr=C2"
            ),
        )

    def test_words_explorer_card_metadata_is_available_without_extra_queries(self):
        UserWordStatus.objects.create(
            user=self.user,
            vocabulary_item=self.item,
            status=UserWordStatus.Status.LEARNING,
            is_saved=True,
        )
        response = self.client.get(reverse("words:index"))
        items = list(response.context["items"])

        with self.assertNumQueries(0):
            self.assertEqual(items[0].movie.title, self.movie.title)
            self.assertEqual(items[0].learning_status, "learning")
            self.assertTrue(items[0].is_saved_for_user)

    def test_words_explorer_returns_results_partial_for_htmx(self):
        response = self.client.get(
            reverse("words:index"),
            {"q": "scrutinize"},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "partials/word_explorer_results.html")
        self.assertTemplateNotUsed(response, "vocabulary/words_explorer.html")
        self.assertContains(response, 'id="word-results"')

    def test_words_explorer_paginates_and_preserves_filters(self):
        for index in range(25):
            VocabularyItem.objects.create(
                **vocabulary_item_fields(movie=self.movie, word=f"term-{index:02d}")
            )

        response = self.client.get(
            reverse("words:index"),
            {"type": VocabularyItem.Type.VERB, "ignored": "do-not-preserve"},
        )

        self.assertEqual(len(response.context["items"]), 24)
        self.assertEqual(response.context["page_obj"].paginator.count, 26)
        self.assertEqual(response.context["filter_query"], "type=verb")
        self.assertContains(response, "type=verb&amp;page=2")
        self.assertNotContains(response, "ignored=do-not-preserve")

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

    def test_delete_cascades_learning_status_for_item(self):
        word_status = UserWordStatus.objects.create(
            user=self.user,
            vocabulary_item=self.item,
            status=UserWordStatus.Status.LEARNING,
            wrong_count=1,
        )

        response = self.client.post(
            reverse("vocabulary:item_delete", args=[self.item.pk]),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(UserWordStatus.objects.filter(pk=word_status.pk).exists())
        self.assertFalse(VocabularyItem.objects.filter(pk=self.item.pk).exists())

    def test_delete_cascades_mastered_status_for_item(self):
        word_status = UserWordStatus.objects.create(
            user=self.user,
            vocabulary_item=self.item,
            status=UserWordStatus.Status.MASTERED,
            correct_count=1,
        )

        response = self.client.post(
            reverse("vocabulary:item_delete", args=[self.item.pk]),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 204)
        self.assertFalse(UserWordStatus.objects.filter(pk=word_status.pk).exists())
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
