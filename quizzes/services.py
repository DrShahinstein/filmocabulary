import random
import re
import unicodedata
from dataclasses import dataclass

from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone

from movies.models import Movie
from vocabulary.models import VocabularyItem

from .models import QuizAttempt, QuizSession

MAX_QUESTIONS = 25
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_answer(value):
    """Normalize only Unicode presentation, case, and whitespace."""
    normalized = unicodedata.normalize("NFKC", value or "")
    return _WHITESPACE_RE.sub(" ", normalized).strip().casefold()


def eligible_questions(movies):
    return (
        VocabularyItem.objects.filter(movie__in=movies, blank_sentence__contains="___")
        .exclude(word_or_phrase="")
        .exclude(word_or_phrase__isnull=True)
        .exclude(blank_sentence="")
        .exclude(blank_sentence__isnull=True)
        .distinct()
    )


@transaction.atomic
def create_quiz_session(*, user, movies, question_count):
    movie_ids = list(movies.values_list("pk", flat=True))
    owned_movies = Movie.objects.filter(user=user, pk__in=movie_ids)
    if owned_movies.count() != len(set(movie_ids)) or not movie_ids:
        raise ValidationError("Select only movies from your own library.")

    question_ids = list(
        eligible_questions(owned_movies).values_list("pk", flat=True)
    )
    if not 1 <= question_count <= MAX_QUESTIONS:
        raise ValidationError(f"Choose between 1 and {MAX_QUESTIONS} questions.")
    if len(question_ids) < question_count:
        raise ValidationError("There are not enough quiz-ready words for this quiz.")

    selected_question_ids = random.SystemRandom().sample(question_ids, question_count)
    session = QuizSession.objects.create(
        user=user,
        total_questions=question_count,
    )
    session.selected_movies.set(owned_movies)
    session.questions.set(selected_question_ids)
    return session


def next_unanswered_question(session):
    answered_ids = session.attempts.values_list("vocabulary_item_id", flat=True)
    return (
        session.questions.select_related("movie")
        .exclude(pk__in=answered_ids)
        .order_by("pk")
        .first()
    )


@dataclass(frozen=True)
class AnswerResult:
    session: QuizSession
    attempt: QuizAttempt
    created: bool
    answered_count: int
    is_complete: bool


@transaction.atomic
def record_answer(*, session, vocabulary_item, submitted_answer):
    locked_session = QuizSession.objects.select_for_update().get(
        pk=session.pk,
        user_id=session.user_id,
    )
    question = locked_session.questions.filter(pk=vocabulary_item.pk).first()
    if question is None:
        raise ValidationError("That question is not part of this quiz.")

    existing_attempt = locked_session.attempts.filter(
        vocabulary_item=question
    ).first()
    if existing_attempt is not None:
        answered_count = locked_session.attempts.count()
        return AnswerResult(
            session=locked_session,
            attempt=existing_attempt,
            created=False,
            answered_count=answered_count,
            is_complete=bool(locked_session.completed_at),
        )

    if locked_session.completed_at:
        raise ValidationError("This quiz has already been completed.")

    current_question = next_unanswered_question(locked_session)
    if current_question is None or current_question.pk != question.pk:
        raise ValidationError("Answer the current question before continuing.")

    is_correct = normalize_answer(submitted_answer) == normalize_answer(
        question.word_or_phrase
    )
    attempt = QuizAttempt.objects.create(
        session=locked_session,
        vocabulary_item=question,
        submitted_answer=submitted_answer.strip(),
        is_correct=is_correct,
    )
    if is_correct:
        locked_session.correct_answers += 1

    answered_count = locked_session.attempts.count()
    is_complete = answered_count >= locked_session.total_questions
    update_fields = ["correct_answers"]
    if is_complete:
        locked_session.completed_at = timezone.now()
        update_fields.append("completed_at")
    locked_session.save(update_fields=update_fields)

    return AnswerResult(
        session=locked_session,
        attempt=attempt,
        created=True,
        answered_count=answered_count,
        is_complete=is_complete,
    )
