import hashlib
import random
import secrets
from dataclasses import dataclass

from django.core import signing
from django.core.cache import cache
from django.db import transaction
from django.db.models import Max, Min, Subquery
from django.db.models.functions import Lower, Trim
from django.utils import timezone

from movies.models import Movie
from vocabulary.models import VocabularyItem
from vocabulary.querysets import (
    VocabularyFilterSpec,
    filter_vocabulary_queryset,
    owned_vocabulary_queryset,
)

from .models import UserWordStatus

OPTION_LABELS = ("A", "B", "C", "D", "E")
DEFINITION_MODE = "definition"
CLOZE_MODE = "cloze"
MIXED_MODE = "mixed"
QUIZ_MODES = (DEFINITION_MODE, CLOZE_MODE, MIXED_MODE)
QUIZ_KINDS = (DEFINITION_MODE, CLOZE_MODE)
TRACKED_POOLS = ("collection", "learning")
TARGETED_POOL = "targeted"
QUIZ_POOLS = (*TRACKED_POOLS, TARGETED_POOL)
QUESTION_SALT = "quizzes.multiple-choice.v1"
TARGETED_SCOPE_SALT = "quizzes.targeted-scope.v1"
QUESTION_MAX_AGE = 60 * 60


class QuizUnavailableError(Exception):
    """Raised when the selected pool cannot produce a complete question."""


class QuizTokenError(Exception):
    """Raised when signed quiz state is invalid, expired, or no longer usable."""


class DuplicateAnswerError(Exception):
    """Raised when an already-answered signed question is submitted again."""


@dataclass(frozen=True)
class QuizOption:
    label: str
    vocabulary_item_id: int
    definition: str
    word_or_phrase: str


@dataclass(frozen=True)
class QuizQuestion:
    target: VocabularyItem
    options: tuple[QuizOption, ...]
    token: str
    pool: str
    mode: str
    kind: str
    movie_ids: tuple[int, ...]
    filter_spec: VocabularyFilterSpec | None
    scope_token: str | None
    is_saved: bool
    run_id: str | None
    round_reset: bool = False

    @property
    def updates_progress(self) -> bool:
        return self.pool in TRACKED_POOLS


# Compatibility for app-local imports written before cloze questions existed.
MultipleChoiceQuestion = QuizQuestion


@dataclass(frozen=True)
class AnswerResult:
    question: QuizQuestion
    selected_option: QuizOption
    is_correct: bool
    word_status: UserWordStatus | None

    @property
    def updates_progress(self) -> bool:
        return self.question.updates_progress


def sign_targeted_scope(*, user, filter_spec: VocabularyFilterSpec) -> str:
    if not getattr(user, "is_authenticated", False) or user.pk is None:
        raise QuizTokenError("Sign in again to start targeted practice.")
    if not isinstance(filter_spec, VocabularyFilterSpec):
        raise QuizTokenError("Choose valid word filters to start targeted practice.")
    return signing.dumps(
        {
            "v": 1,
            "user": user.pk,
            "filters": filter_spec.as_payload(),
        },
        salt=TARGETED_SCOPE_SALT,
        compress=True,
    )


def targeted_scope_from_token(*, user, token: str) -> VocabularyFilterSpec:
    try:
        payload = signing.loads(token, salt=TARGETED_SCOPE_SALT)
    except signing.BadSignature as exc:
        raise QuizTokenError(
            "This filtered practice link is invalid. Return to Words Explorer and try again."
        ) from exc

    try:
        if (
            not isinstance(payload, dict)
            or payload.get("v") != 1
            or payload.get("user") != user.pk
        ):
            raise ValueError
        return VocabularyFilterSpec.from_payload(payload["filters"])
    except (KeyError, TypeError, ValueError) as exc:
        raise QuizTokenError(
            "This filtered practice link is invalid. Return to Words Explorer and try again."
        ) from exc


def _normalise_movie_ids(*, user, movie_ids):
    if movie_ids is None:
        return ()
    try:
        normalized = tuple(sorted(set(movie_ids)))
    except TypeError as exc:
        raise QuizUnavailableError("Choose valid movies from your library.") from exc
    if any(
        not isinstance(movie_id, int) or isinstance(movie_id, bool)
        for movie_id in normalized
    ):
        raise QuizUnavailableError("Choose valid movies from your library.")
    if normalized and Movie.objects.filter(user=user, pk__in=normalized).count() != len(
        normalized
    ):
        raise QuizUnavailableError("Choose valid movies from your library.")
    return normalized


def _normalise_excluded_target_ids(excluded_target_ids):
    try:
        values = tuple(excluded_target_ids)
    except TypeError as exc:
        raise QuizUnavailableError("This practice round is invalid.") from exc

    normalized = []
    seen = set()
    for value in values:
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            raise QuizUnavailableError("This practice round is invalid.")
        if value not in seen:
            normalized.append(value)
            seen.add(value)
    return tuple(normalized)


def _normalise_run_id(run_id):
    if run_id is None:
        return secrets.token_urlsafe(12)
    if (
        not isinstance(run_id, str)
        or not 16 <= len(run_id) <= 64
        or any(not (character.isalnum() or character in "-_") for character in run_id)
    ):
        raise QuizUnavailableError("This practice round is invalid.")
    return run_id


def _owned_vocabulary(user, movie_ids=()):
    queryset = (
        owned_vocabulary_queryset(user)
        .annotate(_trimmed_definition=Trim("definition_en"))
        .exclude(_trimmed_definition="")
    )
    if movie_ids:
        queryset = queryset.filter(movie_id__in=movie_ids)
    return queryset


def _cloze_eligible(queryset):
    return (
        queryset.filter(blank_sentence__isnull=False)
        .annotate(_trimmed_blank=Trim("blank_sentence"))
        .exclude(_trimmed_blank="")
    )


def _has_five_distinct_values(queryset, field_name):
    values = (
        queryset.annotate(_quiz_value=Lower(Trim(field_name)))
        .exclude(_quiz_value="")
        .order_by()
        .values_list("_quiz_value", flat=True)
        .distinct()[:5]
    )
    return len(list(values)) == 5


def targeted_practice_availability(*, user, filter_spec):
    """Return definition/cloze availability for a validated Explorer scope."""
    if not isinstance(filter_spec, VocabularyFilterSpec):
        return False, False
    targets = filter_vocabulary_queryset(_owned_vocabulary(user), filter_spec)
    if not targets.exists():
        return False, False
    owned = _owned_vocabulary(user)
    definition_available = _has_five_distinct_values(owned, "definition_en")
    cloze_available = _cloze_eligible(targets).exists() and (
        _has_five_distinct_values(owned, "word_or_phrase")
    )
    return definition_available, cloze_available


def _random_row(queryset, rng):
    bounds = queryset.order_by().aggregate(minimum=Min("pk"), maximum=Max("pk"))
    if bounds["minimum"] is None:
        return None

    pivot = rng.randint(bounds["minimum"], bounds["maximum"])
    row = queryset.filter(pk__gte=pivot).order_by("pk").first()
    return row or queryset.order_by("pk").first()


def _exclude_seen_targets(*, queryset, user, excluded_ids):
    """Exclude seen rows and duplicate spellings owned through another movie."""
    if not excluded_ids:
        return queryset
    seen_term_keys = (
        owned_vocabulary_queryset(user)
        .filter(pk__in=excluded_ids)
        .annotate(_seen_term_key=Lower(Trim("word_or_phrase")))
        .values("_seen_term_key")
    )
    return (
        queryset.annotate(_target_term_key=Lower(Trim("word_or_phrase")))
        .exclude(pk__in=excluded_ids)
        .exclude(_target_term_key__in=Subquery(seen_term_keys))
    )


def _target_queryset(
    user,
    pool,
    *,
    mode,
    movie_ids=(),
    filter_spec=None,
    excluded_ids=(),
):
    owned = _owned_vocabulary(user, movie_ids).select_related("movie")
    if pool == TARGETED_POOL:
        if filter_spec is None:
            raise QuizUnavailableError("Choose filters in Words Explorer first.")
        targets = filter_vocabulary_queryset(owned, filter_spec)
    elif pool == "learning":
        targets = owned.filter(
            user_statuses__user=user,
            user_statuses__status=UserWordStatus.Status.LEARNING,
        )
    else:
        encountered_ids = UserWordStatus.objects.filter(user=user).exclude(
            status=UserWordStatus.Status.NEW
        ).values("vocabulary_item_id")
        eligible = _cloze_eligible(owned) if mode == CLOZE_MODE else owned
        new_words = _exclude_seen_targets(
            queryset=eligible.exclude(pk__in=encountered_ids),
            user=user,
            excluded_ids=excluded_ids,
        )
        if new_words.exists():
            return new_words
        return _exclude_seen_targets(
            queryset=eligible,
            user=user,
            excluded_ids=excluded_ids,
        )

    if mode == CLOZE_MODE:
        targets = _cloze_eligible(targets)
    return _exclude_seen_targets(
        queryset=targets,
        user=user,
        excluded_ids=excluded_ids,
    )


def _select_distractors(*, user, target, movie_ids, kind, rng):
    value_field = "definition_en" if kind == DEFINITION_MODE else "word_or_phrase"
    owned = _owned_vocabulary(user, movie_ids).exclude(pk=target.pk).annotate(
        _quiz_value=Lower(Trim(value_field))
    ).exclude(_quiz_value="")
    selected = []
    used_values = {getattr(target, value_field).strip().casefold()}

    for prefer_same_type in (True, False):
        candidates = owned
        if prefer_same_type:
            candidates = candidates.filter(type=target.type)

        while len(selected) < 4:
            excluded_ids = [item.pk for item in selected]
            queryset = candidates.exclude(pk__in=excluded_ids).exclude(
                _quiz_value__in=used_values
            )
            candidate = _random_row(queryset, rng)
            if candidate is None:
                break
            normalized_value = getattr(candidate, value_field).strip().casefold()
            if normalized_value in used_values:
                # Unicode case-folding can still differ from database LOWER().
                candidates = candidates.exclude(pk=candidate.pk)
                continue
            selected.append(candidate)
            used_values.add(normalized_value)

        if len(selected) == 4:
            break

    if len(selected) < 4:
        value_label = "definitions" if kind == DEFINITION_MODE else "terms"
        raise QuizUnavailableError(
            f"Add at least five vocabulary entries with distinct {value_label} "
            "to start practice."
        )
    return selected


def _has_cloze(item: VocabularyItem) -> bool:
    return bool(item.blank_sentence and item.blank_sentence.strip())


def _question_kind(*, mode, target, rng):
    if mode == CLOZE_MODE:
        return CLOZE_MODE
    if mode == MIXED_MODE and _has_cloze(target) and rng.choice((False, True)):
        return CLOZE_MODE
    return DEFINITION_MODE


def _empty_pool_message(*, pool, mode):
    if pool == TARGETED_POOL:
        if mode == CLOZE_MODE:
            return "No matching words have a fill-in-the-blank sentence."
        return "No words match this filtered practice set."
    if pool == "learning":
        if mode == CLOZE_MODE:
            return "No Learning Pool words currently have a fill-in-the-blank sentence."
        return "Your Learning Pool is empty. Missed words will appear here automatically."
    if mode == CLOZE_MODE:
        return "Add vocabulary with fill-in-the-blank sentences before starting cloze practice."
    return "Add vocabulary to your library before starting practice."


def generate_question(
    *,
    user,
    pool="collection",
    mode=DEFINITION_MODE,
    target=None,
    movie_ids=None,
    filter_spec=None,
    scope_token=None,
    excluded_target_ids=(),
    run_id=None,
    rng=None,
):
    """Build a signed definition or cloze question from owned vocabulary."""
    if pool not in QUIZ_POOLS:
        raise QuizUnavailableError("Choose a valid practice pool.")
    if mode not in QUIZ_MODES:
        raise QuizUnavailableError("Choose a valid quiz mode.")
    if pool == TARGETED_POOL and mode == MIXED_MODE:
        raise QuizUnavailableError("Choose Definition or Fill-in-the-blanks practice.")

    normalized_movie_ids = _normalise_movie_ids(user=user, movie_ids=movie_ids)
    if pool == TARGETED_POOL:
        if normalized_movie_ids:
            raise QuizUnavailableError("Filtered practice cannot use a separate movie scope.")
        if scope_token is not None:
            signed_spec = targeted_scope_from_token(user=user, token=scope_token)
            if filter_spec is not None and signed_spec != filter_spec:
                raise QuizUnavailableError("The filtered practice scope does not match.")
            filter_spec = signed_spec
        if not isinstance(filter_spec, VocabularyFilterSpec):
            raise QuizUnavailableError("Choose filters in Words Explorer first.")
        scope_token = scope_token or sign_targeted_scope(user=user, filter_spec=filter_spec)
    elif filter_spec is not None or scope_token is not None:
        raise QuizUnavailableError("Choose a valid practice pool.")

    normalized_excluded_ids = _normalise_excluded_target_ids(excluded_target_ids)
    normalized_run_id = _normalise_run_id(run_id)
    rng = rng or random.SystemRandom()

    def select_target(question_mode, excluded_ids):
        targets = _target_queryset(
            user,
            pool,
            mode=question_mode,
            movie_ids=normalized_movie_ids,
            filter_spec=filter_spec,
            excluded_ids=excluded_ids,
        )
        return _random_row(targets, rng)

    targets = _target_queryset(
        user,
        pool,
        mode=mode,
        movie_ids=normalized_movie_ids,
        filter_spec=filter_spec,
        excluded_ids=normalized_excluded_ids,
    )
    round_reset = False
    if target is None:
        target = _random_row(targets, rng)
        if target is None and normalized_excluded_ids:
            # Start a new randomized round. Avoid an immediate boundary repeat
            # whenever the eligible pool contains more than one distinct term.
            target = select_target(mode, normalized_excluded_ids[-1:])
            if target is None:
                target = select_target(mode, ())
            round_reset = target is not None
    else:
        if pool in ("learning", TARGETED_POOL) or mode == CLOZE_MODE:
            allowed_targets = targets
        else:
            allowed_targets = _owned_vocabulary(
                user, normalized_movie_ids
            ).select_related("movie")
        target = allowed_targets.filter(pk=target.pk).first()
        if target is None:
            raise QuizUnavailableError("That word is not available in this practice pool.")

    if target is None:
        raise QuizUnavailableError(_empty_pool_message(pool=pool, mode=mode))

    kind = _question_kind(mode=mode, target=target, rng=rng)
    option_movie_ids = () if pool == TARGETED_POOL else normalized_movie_ids
    try:
        distractors = _select_distractors(
            user=user,
            target=target,
            movie_ids=option_movie_ids,
            kind=kind,
            rng=rng,
        )
    except QuizUnavailableError:
        if mode != MIXED_MODE:
            raise
        alternative_kind = (
            CLOZE_MODE if kind == DEFINITION_MODE else DEFINITION_MODE
        )
        alternative_targets = _target_queryset(
            user,
            pool,
            mode=alternative_kind,
            movie_ids=normalized_movie_ids,
            filter_spec=filter_spec,
            excluded_ids=normalized_excluded_ids,
        )
        target = _random_row(alternative_targets, rng)
        alternative_round_reset = False
        if target is None and normalized_excluded_ids:
            target = select_target(alternative_kind, normalized_excluded_ids[-1:])
            if target is None:
                target = select_target(alternative_kind, ())
            alternative_round_reset = target is not None
        if target is None:
            raise
        round_reset = round_reset or alternative_round_reset
        kind = alternative_kind
        distractors = _select_distractors(
            user=user,
            target=target,
            movie_ids=option_movie_ids,
            kind=kind,
            rng=rng,
        )

    option_items = [target, *distractors]
    rng.shuffle(option_items)

    option_ids = [item.pk for item in option_items]
    token = signing.dumps(
        {
            "v": 4,
            "target": target.pk,
            "options": option_ids,
            "pool": pool,
            "mode": mode,
            "kind": kind,
            "movies": list(normalized_movie_ids),
            "filters": filter_spec.as_payload() if filter_spec is not None else None,
            "run": normalized_run_id,
            "nonce": secrets.token_urlsafe(8),
        },
        salt=QUESTION_SALT,
        compress=True,
    )
    options = tuple(
        QuizOption(label, item.pk, item.definition_en, item.word_or_phrase)
        for label, item in zip(
            OPTION_LABELS[: len(option_items)],
            option_items,
            strict=True,
        )
    )
    return QuizQuestion(
        target=target,
        options=options,
        token=token,
        pool=pool,
        mode=mode,
        kind=kind,
        movie_ids=normalized_movie_ids,
        filter_spec=filter_spec,
        scope_token=scope_token,
        is_saved=bool(target.is_saved_for_user),
        run_id=normalized_run_id,
        round_reset=round_reset,
    )


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
        if not isinstance(payload, dict):
            raise TypeError
        version = payload.get("v", 1)
        target_id = payload["target"]
        option_ids = payload["options"]
        pool = payload["pool"]
        mode = payload.get("mode", DEFINITION_MODE)
        kind = payload.get("kind", DEFINITION_MODE)
        movie_ids = payload.get("movies", [])
        filters_payload = payload.get("filters")
        run_id = payload.get("run")
    except (KeyError, TypeError) as exc:
        raise QuizTokenError("This question is invalid. Load a new one to continue.") from exc

    valid_scalars = (
        isinstance(version, int)
        and not isinstance(version, bool)
        and version in (1, 2, 3, 4)
        and isinstance(target_id, int)
        and not isinstance(target_id, bool)
        and pool in QUIZ_POOLS
        and mode in QUIZ_MODES
        and kind in QUIZ_KINDS
        and not (mode == DEFINITION_MODE and kind != DEFINITION_MODE)
        and not (mode == CLOZE_MODE and kind != CLOZE_MODE)
        and not (
            version == 1
            and (mode != DEFINITION_MODE or kind != DEFINITION_MODE)
        )
        and isinstance(option_ids, list)
        and isinstance(movie_ids, list)
        and all(
            isinstance(value, int) and not isinstance(value, bool)
            for value in movie_ids
        )
        and len(set(movie_ids)) == len(movie_ids)
        and (
            (
                version == 4
                and isinstance(run_id, str)
                and 16 <= len(run_id) <= 64
                and all(
                    character.isalnum() or character in "-_"
                    for character in run_id
                )
            )
            or (version in (1, 2, 3) and run_id is None)
        )
    )
    valid_options = (
        len(option_ids) == 5
        and all(
            isinstance(value, int) and not isinstance(value, bool)
            for value in option_ids
        )
        and len(set(option_ids)) == 5
        and target_id in option_ids
    )
    if not valid_scalars or not valid_options:
        raise QuizTokenError("This question is invalid. Load a new one to continue.")

    try:
        normalized_movie_ids = _normalise_movie_ids(user=user, movie_ids=movie_ids)
        filter_spec = (
            VocabularyFilterSpec.from_payload(filters_payload)
            if filters_payload is not None
            else None
        )
    except (QuizUnavailableError, TypeError, ValueError) as exc:
        raise QuizTokenError("This question is invalid. Load a new one to continue.") from exc

    valid_scope = (
        pool == TARGETED_POOL
        and version in (2, 3, 4)
        and mode != MIXED_MODE
        and not normalized_movie_ids
        and filter_spec is not None
    ) or (pool in TRACKED_POOLS and filter_spec is None)
    if not valid_scope:
        raise QuizTokenError("This question is invalid. Load a new one to continue.")

    requested_ids = option_ids
    item_movie_ids = () if pool == TARGETED_POOL else normalized_movie_ids
    items = {
        item.pk: item
        for item in _owned_vocabulary(user, item_movie_ids)
        .filter(pk__in=requested_ids)
        .select_related("movie")
    }
    if len(items) != len(requested_ids):
        raise QuizTokenError(
            "Part of this question is no longer available. Load a new one to continue."
        )

    target = items[target_id]
    if kind == CLOZE_MODE and not _has_cloze(target):
        raise QuizTokenError(
            "This fill-in-the-blank question is no longer available. Load a new one to continue."
        )
    options = tuple(
        QuizOption(
            label,
            item_id,
            items[item_id].definition_en,
            items[item_id].word_or_phrase,
        )
        for label, item_id in zip(
            OPTION_LABELS[: len(option_ids)],
            option_ids,
            strict=True,
        )
    )
    scope_token = (
        sign_targeted_scope(user=user, filter_spec=filter_spec)
        if filter_spec is not None
        else None
    )
    return QuizQuestion(
        target=target,
        options=options,
        token=token,
        pool=pool,
        mode=mode,
        kind=kind,
        movie_ids=normalized_movie_ids,
        filter_spec=filter_spec,
        scope_token=scope_token,
        is_saved=bool(target.is_saved_for_user),
        run_id=run_id,
    )


def skip_question(*, user, token, expected_pool=None, excluded_target_ids=()):
    """Return another question in the same signed scope without changing progress."""
    current = question_from_token(user=user, token=token)
    if expected_pool is not None and current.pool != expected_pool:
        raise QuizTokenError("This question belongs to a different practice pool.")
    history = list(_normalise_excluded_target_ids(excluded_target_ids))
    if current.target.pk in history:
        history.remove(current.target.pk)
    history.append(current.target.pk)
    return generate_question(
        user=user,
        pool=current.pool,
        mode=current.mode,
        movie_ids=current.movie_ids,
        filter_spec=current.filter_spec,
        scope_token=current.scope_token,
        excluded_target_ids=history,
        run_id=current.run_id,
    )


@transaction.atomic
def toggle_saved_word(*, user, vocabulary_item_id):
    """Toggle a user-owned word bookmark without changing its learning status."""
    vocabulary_item = (
        VocabularyItem.objects.filter(
            pk=vocabulary_item_id,
            movie__user=user,
        )
        .select_related("movie")
        .first()
    )
    if vocabulary_item is None:
        raise VocabularyItem.DoesNotExist

    user.__class__.objects.select_for_update().get(pk=user.pk)
    word_status, _ = UserWordStatus.objects.get_or_create(
        user=user,
        vocabulary_item=vocabulary_item,
    )
    word_status.vocabulary_item = vocabulary_item
    word_status.is_saved = not word_status.is_saved
    word_status.save(update_fields=("is_saved",))
    return vocabulary_item, word_status


@transaction.atomic
def answer_question(
    *,
    user,
    token,
    selected_item_id=None,
):
    question = question_from_token(user=user, token=token)
    options_by_id = {
        option.vocabulary_item_id: option for option in question.options
    }
    if (
        not isinstance(selected_item_id, int)
        or isinstance(selected_item_id, bool)
        or selected_item_id not in options_by_id
    ):
        raise QuizTokenError("Choose one of the five answers shown.")
    selected_option = options_by_id[selected_item_id]
    is_correct = selected_item_id == question.target.pk

    token_digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    cache_key = f"quiz-answer:{user.pk}:{token_digest}"
    if not cache.add(cache_key, True, timeout=QUESTION_MAX_AGE):
        raise DuplicateAnswerError("That question has already been answered.")

    status = None
    try:
        if question.updates_progress:
            # Serialize answers per user so concurrent tabs cannot race the counters.
            user.__class__.objects.select_for_update().get(pk=user.pk)
            status, _ = UserWordStatus.objects.get_or_create(
                user=user,
                vocabulary_item=question.target,
            )
            if is_correct:
                status.correct_count += 1
                status.status = UserWordStatus.Status.MASTERED
            else:
                status.wrong_count += 1
                status.status = UserWordStatus.Status.LEARNING
            status.last_tested_at = timezone.now()
            status.save(
                update_fields=(
                    "correct_count",
                    "wrong_count",
                    "status",
                    "last_tested_at",
                )
            )
    except Exception:
        cache.delete(cache_key)
        raise

    return AnswerResult(
        question=question,
        selected_option=selected_option,
        is_correct=is_correct,
        word_status=status,
    )
