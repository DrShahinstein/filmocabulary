from django.test import TestCase, override_settings
from django.urls import reverse

from quizzes.models import QuizAttempt, QuizSession
from quizzes.services import next_unanswered_question

from .factories import make_movie, make_user, make_vocabulary


TEST_STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {
        "BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"
    },
}


@override_settings(
    ROOT_URLCONF="quizzes.tests.urls",
    STORAGES=TEST_STORAGES,
)
class QuizViewTests(TestCase):
    def setUp(self):
        self.user = make_user("learner")
        self.other_user = make_user("other")
        self.movie = make_movie(self.user)
        self.other_movie = make_movie(self.other_user, "Heat", 1995)
        self.words = [
            make_vocabulary(self.movie, "meticulous"),
            make_vocabulary(self.movie, "conundrum", "The case presented a ___."),
        ]
        self.outsider_word = make_vocabulary(
            self.other_movie,
            "elusive",
            "The lead remained ___.",
        )

    def make_session(self, *, user=None, movie=None, words=None):
        user = user or self.user
        movie = movie or self.movie
        words = words or self.words
        session = QuizSession.objects.create(user=user, total_questions=len(words))
        session.selected_movies.add(movie)
        session.questions.set(words)
        return session

    def test_start_requires_authentication(self):
        response = self.client.get(reverse("quizzes:start"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url)

    def test_builder_requires_authentication(self):
        response = self.client.get(reverse("quizzes:builder"))

        self.assertEqual(response.status_code, 302)
        self.assertIn("login", response.url)

    def test_builder_renders_owned_movie_choices(self):
        self.client.force_login(self.user)

        response = self.client.get(reverse("quizzes:builder"))

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "partials/quiz_start_form.html")
        self.assertContains(response, self.movie.title)
        self.assertNotContains(response, self.other_movie.title)

    def test_htmx_start_creates_session_and_returns_first_question(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("quizzes:start"),
            {"movies": [self.movie.pk], "question_count": 2},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        session = QuizSession.objects.get()
        self.assertEqual(session.questions.count(), 2)
        self.assertEqual(
            response.headers["HX-Push-Url"],
            reverse("quizzes:play", kwargs={"pk": session.pk}),
        )
        self.assertContains(response, "Question 1 of 2")

    def test_start_rejects_forged_movie_ownership(self):
        self.client.force_login(self.user)
        response = self.client.post(
            reverse("quizzes:start"),
            {"movies": [self.other_movie.pk], "question_count": 1},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(QuizSession.objects.exists())
        self.assertContains(response, "Select a valid choice")

    def test_user_cannot_open_another_users_session(self):
        session = self.make_session(
            user=self.other_user,
            movie=self.other_movie,
            words=[self.outsider_word],
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("quizzes:play", args=[session.pk]))

        self.assertEqual(response.status_code, 404)

    def test_answer_returns_immediate_feedback_and_updates_score(self):
        session = self.make_session()
        question = next_unanswered_question(session)
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("quizzes:answer", args=[session.pk]),
            {
                "vocabulary_item": question.pk,
                "submitted_answer": question.word_or_phrase.upper(),
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Correct")
        self.assertContains(response, "Next question")
        session.refresh_from_db()
        self.assertEqual(session.correct_answers, 1)

    def test_duplicate_answer_does_not_change_the_recorded_result(self):
        session = self.make_session()
        question = next_unanswered_question(session)
        self.client.force_login(self.user)
        url = reverse("quizzes:answer", args=[session.pk])
        payload = {
            "vocabulary_item": question.pk,
            "submitted_answer": question.word_or_phrase,
        }
        self.client.post(url, payload, HTTP_HX_REQUEST="true")
        payload["submitted_answer"] = "unrelated"

        response = self.client.post(url, payload, HTTP_HX_REQUEST="true")

        self.assertContains(response, "already recorded")
        self.assertEqual(QuizAttempt.objects.filter(session=session).count(), 1)
        session.refresh_from_db()
        self.assertEqual(session.correct_answers, 1)

    def test_forged_question_is_not_recorded(self):
        session = self.make_session()
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("quizzes:answer", args=[session.pk]),
            {
                "vocabulary_item": self.outsider_word.pk,
                "submitted_answer": self.outsider_word.word_or_phrase,
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(session.attempts.exists())
        self.assertContains(response, "Select a valid choice")

    def test_history_only_lists_current_users_sessions(self):
        own_session = self.make_session()
        other_session = self.make_session(
            user=self.other_user,
            movie=self.other_movie,
            words=[self.outsider_word],
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("quizzes:history"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, self.movie.title)
        self.assertNotContains(response, self.other_movie.title)
        self.assertIn(own_session, response.context["sessions"])
        self.assertNotIn(other_session, response.context["sessions"])
