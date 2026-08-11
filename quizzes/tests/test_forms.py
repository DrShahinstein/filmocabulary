from django.test import TestCase

from quizzes.forms import QuizAnswerForm, QuizStartForm
from quizzes.models import QuizSession

from .factories import make_movie, make_user, make_vocabulary


class QuizStartFormTests(TestCase):
    def setUp(self):
        self.user = make_user("learner")
        self.other_user = make_user("other")
        self.movie = make_movie(self.user)
        self.other_movie = make_movie(self.other_user, "Heat", 1995)
        make_vocabulary(self.movie)
        make_vocabulary(self.movie, "conundrum", "The case presented a ___.")
        make_vocabulary(self.other_movie, "elusive", "The lead remained ___.")

    def test_accepts_owned_movies_with_enough_questions(self):
        form = QuizStartForm(
            data={"movies": [self.movie.pk], "question_count": 2},
            user=self.user,
        )

        self.assertTrue(form.is_valid(), form.errors)

    def test_rejects_another_users_movie(self):
        form = QuizStartForm(
            data={"movies": [self.other_movie.pk], "question_count": 1},
            user=self.user,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("movies", form.errors)

    def test_rejects_question_count_above_available_pool(self):
        form = QuizStartForm(
            data={"movies": [self.movie.pk], "question_count": 3},
            user=self.user,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("question_count", form.errors)


class QuizAnswerFormTests(TestCase):
    def test_limits_item_to_session_questions(self):
        user = make_user("learner")
        movie = make_movie(user)
        included = make_vocabulary(movie)
        excluded = make_vocabulary(movie, "conundrum", "The case presented a ___.")
        session = QuizSession.objects.create(user=user, total_questions=1)
        session.selected_movies.add(movie)
        session.questions.add(included)

        form = QuizAnswerForm(
            data={
                "vocabulary_item": excluded.pk,
                "submitted_answer": excluded.word_or_phrase,
            },
            session=session,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("vocabulary_item", form.errors)

