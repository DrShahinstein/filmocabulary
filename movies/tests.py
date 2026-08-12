import warnings

from django.contrib.auth import get_user_model
from django.db import IntegrityError, transaction
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from quizzes.models import QuizAttempt, QuizSession
from vocabulary.models import VocabularyItem

from .models import Movie


class DashboardTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="owner", password="test-password-42"
        )

    def test_dashboard_requires_login(self):
        response = self.client.get(reverse("movies:dashboard"))
        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('movies:dashboard')}",
        )

    def test_dashboard_only_lists_owned_movies(self):
        other = get_user_model().objects.create_user(
            username="other", password="test-password-42"
        )
        owned = Movie.objects.create(user=self.user, title="Arrival", release_year=2016)
        Movie.objects.create(user=other, title="Zodiac", release_year=2007)
        self.client.force_login(self.user)

        response = self.client.get(reverse("movies:dashboard"))

        self.assertContains(response, owned.title)
        self.assertQuerySetEqual(response.context["movies"], [owned])

    def test_dashboard_movie_cards_render_without_csrf_warning(self):
        Movie.objects.create(user=self.user, title="Arrival", release_year=2016)
        self.client.force_login(self.user)

        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always")
            response = self.client.get(reverse("movies:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertFalse(
            any("{% csrf_token %}" in str(warning.message) for warning in caught)
        )

    def test_dashboard_movie_card_uses_dynamic_cefr_badge(self):
        movie = Movie.objects.create(
            user=self.user,
            title="Arrival",
            release_year=2016,
        )
        for word, level in (("adapt", "B1"), ("scrutinize", "B2")):
            VocabularyItem.objects.create(
                movie=movie,
                word_or_phrase=word,
                type=VocabularyItem.Type.VERB,
                cefr_level=level,
                definition_en=f"Definition for {word}.",
                example_sentence=f"They must {word} the plan.",
                blank_sentence="They must ___ the plan.",
            )
        self.client.force_login(self.user)

        response = self.client.get(reverse("movies:dashboard"))

        self.assertContains(response, "≈B1-B2")
        self.assertNotContains(response, ">B2-C2</span>", html=False)

    def test_dashboard_movie_card_handles_no_saved_vocabulary(self):
        Movie.objects.create(user=self.user, title="Arrival", release_year=2016)
        self.client.force_login(self.user)

        response = self.client.get(reverse("movies:dashboard"))

        self.assertContains(response, "No saved vocabulary")
        self.assertContains(response, "—")


class MovieConstraintTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(username="constraint-owner")

    def test_title_and_year_are_unique_case_insensitively_per_user(self):
        Movie.objects.create(user=self.user, title="Arrival", release_year=2016)

        with self.assertRaises(IntegrityError), transaction.atomic():
            Movie.objects.create(user=self.user, title="ARRIVAL", release_year=2016)

    def test_title_without_year_is_unique_case_insensitively_per_user(self):
        Movie.objects.create(user=self.user, title="Zodiac")

        with self.assertRaises(IntegrityError), transaction.atomic():
            Movie.objects.create(user=self.user, title="zodiac")


class MovieCefrBadgeTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        user = get_user_model().objects.create_user(username="level-owner")
        cls.movie = Movie.objects.create(user=user, title="Arrival", release_year=2016)

    def add_item(self, word, level):
        return VocabularyItem.objects.create(
            movie=self.movie,
            word_or_phrase=word,
            type=VocabularyItem.Type.VERB,
            cefr_level=level,
            definition_en=f"Definition for {word}.",
            example_sentence=f"They must {word} the plan.",
            blank_sentence="They must ___ the plan.",
        )

    def test_empty_collection_has_no_level(self):
        self.assertEqual(self.movie.vocabulary_cefr_badge, "—")

    def test_equal_adjacent_levels_render_as_range(self):
        self.add_item("adapt", "B1")
        self.add_item("scrutinize", "B2")

        self.assertEqual(self.movie.vocabulary_cefr_badge, "≈B1-B2")

    def test_collection_weighted_toward_one_level_renders_single_level(self):
        for word in ("adapt", "abandon", "absorb"):
            self.add_item(word, "B2")
        self.add_item("scrutinize", "C1")

        self.assertEqual(self.movie.vocabulary_cefr_badge, "≈B2")


class MovieDeletionTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_user(
            username="deletion-owner",
            password="test-password-42",
        )
        self.other_user = get_user_model().objects.create_user(
            username="other-deletion-owner",
            password="test-password-42",
        )
        self.movie = Movie.objects.create(
            user=self.user,
            title="Zodiac",
            release_year=2007,
            filtered_subtitle_text="We must scrutinize every detail.",
            subtitle_cache_version=1,
        )
        self.item = VocabularyItem.objects.create(
            movie=self.movie,
            word_or_phrase="scrutinize",
            type=VocabularyItem.Type.VERB,
            cefr_level=VocabularyItem.CefrLevel.C1,
            definition_en="Examine carefully.",
            example_sentence="Investigators scrutinize every detail.",
            blank_sentence="Investigators ___ every detail.",
        )
        self.quiz = QuizSession.objects.create(
            user=self.user,
            total_questions=1,
            correct_answers=1,
            completed_at=timezone.now(),
        )
        self.quiz.selected_movies.add(self.movie)
        self.quiz.questions.add(self.item)
        QuizAttempt.objects.create(
            session=self.quiz,
            vocabulary_item=self.item,
            submitted_answer="scrutinize",
            is_correct=True,
        )
        self.client.force_login(self.user)

    def test_htmx_delete_removes_owned_movie_and_related_data(self):
        response = self.client.post(
            reverse("movies:delete", args=[self.movie.pk]),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(Movie.objects.filter(pk=self.movie.pk).exists())
        self.assertFalse(VocabularyItem.objects.filter(pk=self.item.pk).exists())
        self.assertFalse(QuizSession.objects.filter(pk=self.quiz.pk).exists())
        self.assertContains(response, 'id="movie-library"')
        self.assertContains(response, "Your library is empty")
        self.assertEqual(
            response.headers["HX-Trigger-After-Swap"],
            "vocabularyChanged, movieDeleted",
        )

    def test_delete_is_scoped_to_owner(self):
        other_movie = Movie.objects.create(
            user=self.other_user,
            title="Arrival",
            release_year=2016,
        )

        response = self.client.post(
            reverse("movies:delete", args=[other_movie.pk]),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 404)
        self.assertTrue(Movie.objects.filter(pk=other_movie.pk).exists())

    def test_delete_requires_post(self):
        response = self.client.get(reverse("movies:delete", args=[self.movie.pk]))

        self.assertEqual(response.status_code, 405)
        self.assertTrue(Movie.objects.filter(pk=self.movie.pk).exists())

    def test_standard_post_redirects_with_success_message(self):
        response = self.client.post(reverse("movies:delete", args=[self.movie.pk]))

        self.assertRedirects(response, reverse("movies:dashboard"))
        messages = list(response.wsgi_request._messages)
        self.assertEqual(
            str(messages[0]),
            '"Zodiac" was deleted from your library.',
        )

    def test_delete_requires_authentication(self):
        self.client.logout()

        response = self.client.post(reverse("movies:delete", args=[self.movie.pk]))

        self.assertRedirects(
            response,
            f"{reverse('login')}?next={reverse('movies:delete', args=[self.movie.pk])}",
        )
        self.assertTrue(Movie.objects.filter(pk=self.movie.pk).exists())
