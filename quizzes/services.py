import hashlib
import random
import secrets
from dataclasses import dataclass

from django.core import signing
from django.core.cache import cache
from django.db import transaction
from django.db.models import Max, Min
from django.db.models.functions import Lower, Trim
from django.utils import timezone

from vocabulary.models import VocabularyItem

from .models import UserWordStatus

OPTION_LABELS = ("A", "B", "C", "D", "E")
QUIZ_POOLS = ("collection", "learning")
QUESTION_SALT = "quizzes.multiple-choice.v1"
QUESTION_MAX_AGE = 60 * 60


class QuizUnavailableError(Exception):
    """Raised when the selected pool cannot produce a complete MCQ."""


class QuizTokenError(Exception):
    """Raised when a signed question is invalid, expired, or no longer usable."""


class DuplicateAnswerError(Exception):
    """Raised when an already-scored signed question is submitted again."""


@dataclass(frozen=True)
class QuizOption:
    label: str
    vocabulary_item_id: int
    definition: str


@dataclass(frozen=True)
class MultipleChoiceQuestion:
    target: VocabularyItem
    options: tuple[QuizOption, ...]
    token: str
    pool: str


@dataclass(frozen=True)
class AnswerResult:
    question: MultipleChoiceQuestion
    selected_option: QuizOption
    is_correct: bool
    word_status: UserWordStatus


def _owned_vocabulary(user):
    return (
        VocabularyItem.objects.filter(movie__user=user)
        .annotate(_trimmed_definition=Trim("definition_en"))
        .exclude(_trimmed_definition="")
    )


def _random_row(queryset, rng):
    bounds = queryset.order_by().aggregate(minimum=Min("pk"), maximum=Max("pk"))
    if bounds["minimum"] is None:
        return None

    pivot = rng.randint(bounds["minimum"], bounds["maximum"])
    row = queryset.filter(pk__gte=pivot).order_by("pk").first()
    return row or queryset.order_by("pk").first()


def _target_queryset(user, pool):
    owned = _owned_vocabulary(user).select_related("movie")
    if pool == "learning":
        return owned.filter(
            user_statuses__user=user,
            user_statuses__status=UserWordStatus.Status.LEARNING,
        )

    encountered_ids = UserWordStatus.objects.filter(user=user).exclude(
        status=UserWordStatus.Status.NEW
    ).values("vocabulary_item_id")
    new_words = owned.exclude(pk__in=encountered_ids)
    return new_words if new_words.exists() else owned


def _select_distractors(*, user, target, rng):
    owned = _owned_vocabulary(user).exclude(pk=target.pk).annotate(
        _definition_key=Lower(Trim("definition_en"))
    )
    selected = []
    used_definitions = {target.definition_en.strip().casefold()}

    for prefer_same_type in (True, False):
        candidates = owned
        if prefer_same_type:
            candidates = candidates.filter(type=target.type)

        while len(selected) < 4:
            excluded_ids = [item.pk for item in selected]
            queryset = candidates.exclude(pk__in=excluded_ids).exclude(
                _definition_key__in=used_definitions
            )
            candidate = _random_row(queryset, rng)
            if candidate is None:
                break
            normalized_definition = candidate.definition_en.strip().casefold()
            if normalized_definition in used_definitions:
                # Unicode case-folding can still differ from database LOWER().
                candidates = candidates.exclude(pk=candidate.pk)
                continue
            selected.append(candidate)
            used_definitions.add(normalized_definition)

        if len(selected) == 4:
            break

    if len(selected) < 4:
        raise QuizUnavailableError(
            "Add at least five vocabulary entries with distinct definitions to start practice."
        )
    return selected


def generate_question(*, user, pool="collection", target=None, rng=None):
    """Build a five-option MCQ entirely from vocabulary already in the database."""
    if pool not in QUIZ_POOLS:
        raise QuizUnavailableError("Choose a valid practice pool.")

    rng = rng or random.SystemRandom()
    targets = _target_queryset(user, pool)
    if target is None:
        target = _random_row(targets, rng)
    else:
        allowed_targets = targets if pool == "learning" else _owned_vocabulary(user)
        if not allowed_targets.filter(pk=target.pk).exists():
            raise QuizUnavailableError("That word is not available in this practice pool.")

    if target is None:
        message = (
            "Your Learning Pool is empty. Missed words will appear here automatically."
            if pool == "learning"
            else "Add vocabulary to your library before starting practice."
        )
        raise QuizUnavailableError(message)

    distractors = _select_distractors(user=user, target=target, rng=rng)
    option_items = [target, *distractors]
    rng.shuffle(option_items)
    option_ids = [item.pk for item in option_items]
    token = signing.dumps(
        {
            "target": target.pk,
            "options": option_ids,
            "pool": pool,
            "nonce": secrets.token_urlsafe(8),
        },
        salt=QUESTION_SALT,
        compress=True,
    )
    options = tuple(
        QuizOption(label, item.pk, item.definition_en)
        for label, item in zip(OPTION_LABELS, option_items, strict=True)
    )
    return MultipleChoiceQuestion(target, options, token, pool)


def question_from_token(*, user, token):
    try:
        payload = signing.loads(
            token,
            salt=QUESTION_SALT,
            max_age=QUESTION_MAX_AGE,
        )
    except signing.BadSignature as exc:
        raise QuizTokenError("This question has expired. Load a new one to continue.") from exc

    try:
        target_id = payload["target"]
        option_ids = payload["options"]
        pool = payload["pool"]
    except (KeyError, TypeError) as exc:
        raise QuizTokenError("This question is invalid. Load a new one to continue.") from exc

    valid_ids = (
        isinstance(target_id, int)
        and not isinstance(target_id, bool)
        and isinstance(option_ids, list)
        and len(option_ids) == 5
        and all(
            isinstance(value, int) and not isinstance(value, bool)
            for value in option_ids
        )
        and len(set(option_ids)) == 5
        and target_id in option_ids
        and pool in QUIZ_POOLS
    )
    if not valid_ids:
        raise QuizTokenError("This question is invalid. Load a new one to continue.")

    items = {
        item.pk: item
        for item in _owned_vocabulary(user)
        .filter(pk__in=option_ids)
        .select_related("movie")
    }
    if len(items) != 5:
        raise QuizTokenError(
            "Part of this question is no longer available. Load a new one to continue."
        )

    target = items[target_id]
    options = tuple(
        QuizOption(label, item_id, items[item_id].definition_en)
        for label, item_id in zip(OPTION_LABELS, option_ids, strict=True)
    )
    return MultipleChoiceQuestion(target, options, token, pool)


@transaction.atomic
def answer_question(*, user, token, selected_item_id):
    question = question_from_token(user=user, token=token)
    options_by_id = {option.vocabulary_item_id: option for option in question.options}
    if selected_item_id not in options_by_id:
        raise QuizTokenError("Choose one of the five answers shown.")

    token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    cache_key = f"quiz-answer:{user.pk}:{token_digest}"
    if not cache.add(cache_key, True, timeout=QUESTION_MAX_AGE):
        raise DuplicateAnswerError("That question has already been scored.")

    try:
        # Serialize answers per user so concurrent tabs cannot race the counters.
        user.__class__.objects.select_for_update().get(pk=user.pk)
        status, _ = UserWordStatus.objects.get_or_create(
            user=user,
            vocabulary_item=question.target,
        )
        is_correct = selected_item_id == question.target.pk
        if is_correct:
            status.correct_count += 1
            status.status = UserWordStatus.Status.MASTERED
        else:
            status.wrong_count += 1
            status.status = UserWordStatus.Status.LEARNING
        status.last_tested_at = timezone.now()
        status.save(
            update_fields=("correct_count", "wrong_count", "status", "last_tested_at")
        )
    except Exception:
        cache.delete(cache_key)
        raise

    return AnswerResult(
        question=question,
        selected_option=options_by_id[selected_item_id],
        is_correct=is_correct,
        word_status=status,
    )
