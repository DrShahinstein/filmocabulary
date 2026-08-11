from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from movies.models import Movie
from quizzes.models import QuizAttempt, QuizSession
from quizzes.services import (
    create_quiz_session,
    next_unanswered_question,
    normalize_answer,
    record_answer,
)

from .factories import make_movie, make_user, make_vocabulary


class AnswerNormalizationTests(TestCase):
    def test_normalizes_case_and_runs_of_whitespace(self):
        self.assertEqual(normalize_answer("  Carry   OUT "), "carry out")

    def test_does_not_discard_punctuation(self):
        self.assertNotEqual(normalize_answer("carry-out"), normalize_answer("carry out"))


class QuizServiceTests(TestCase):
    def setUp(self):
        self.user = make_user("learner")
        self.movie = make_movie(self.user)
        self.words = [
            make_vocabulary(self.movie, "meticulous"),
            make_vocabulary(self.movie, "conundrum", "The case presented a ___."),
            make_vocabulary(self.movie, "carry out", "They agreed to ___ the plan."),
        ]

    def make_session(self, count=2):
        session = QuizSession.objects.create(user=self.user, total_questions=count)
        session.selected_movies.add(self.movie)
        session.questions.set(self.words[:count])
        return session

    def test_create_session_samples_requested_number_from_owned_movies(self):
        session = create_quiz_session(
            user=self.user,
            movies=Movie.objects.filter(pk=self.movie.pk),
            question_count=2,
        )

        self.assertEqual(session.questions.count(), 2)
        self.assertEqual(session.total_questions, 2)
        self.assertEqual(list(session.selected_movies.all()), [self.movie])
        self.assertTrue(
            set(session.questions.values_list("pk", flat=True)).issubset(
                {word.pk for word in self.words}
            )
        )

    def test_record_answer_scores_case_and_whitespace_normalized_answer(self):
        session = self.make_session(count=1)
        self.assertEqual(session.status, "active")
        question = next_unanswered_question(session)

        result = record_answer(
            session=session,
            vocabulary_item=question,
            submitted_answer=f"  {question.word_or_phrase.upper()}  ",
        )

        self.assertTrue(result.attempt.is_correct)
        self.assertTrue(result.is_complete)
        session.refresh_from_db()
        self.assertEqual(session.correct_answers, 1)
        self.assertIsNotNone(session.completed_at)
        self.assertEqual(session.status, "completed")

    def test_duplicate_submission_returns_original_attempt_without_rescoring(self):
        session = self.make_session(count=2)
        question = next_unanswered_question(session)
        first = record_answer(
            session=session,
            vocabulary_item=question,
            submitted_answer=question.word_or_phrase,
        )
        duplicate = record_answer(
            session=session,
            vocabulary_item=question,
            submitted_answer="unrelated",
        )

        self.assertTrue(first.created)
        self.assertFalse(duplicate.created)
        self.assertEqual(duplicate.attempt.pk, first.attempt.pk)
        self.assertEqual(QuizAttempt.objects.filter(session=session).count(), 1)
        session.refresh_from_db()
        self.assertEqual(session.correct_answers, 1)

    def test_cannot_skip_the_current_question(self):
        session = self.make_session(count=2)
        current = next_unanswered_question(session)
        later = session.questions.exclude(pk=current.pk).get()

        with self.assertRaises(ValidationError):
            record_answer(
                session=session,
                vocabulary_item=later,
                submitted_answer=later.word_or_phrase,
            )

        self.assertFalse(session.attempts.exists())

    def test_database_rejects_duplicate_attempt_for_same_item(self):
        session = self.make_session(count=1)
        question = next_unanswered_question(session)
        QuizAttempt.objects.create(
            session=session,
            vocabulary_item=question,
            submitted_answer="first",
            is_correct=False,
        )

        with self.assertRaises(IntegrityError):
            with transaction.atomic():
                QuizAttempt.objects.create(
                    session=session,
                    vocabulary_item=question,
                    submitted_answer="second",
                    is_correct=False,
                )
