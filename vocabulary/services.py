import logging
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from pydantic import ValidationError as PydanticValidationError

from movies.models import Movie

from .constants import GENERATION_CANDIDATE_SURPLUS, MAX_GENERATION_ITEMS
from .ingestion import SourceDocument
from .matching import source_contains_term
from .models import VocabularyItem
from .providers import (
    SYSTEM_PROMPT,
    ProviderConfigurationError,
    ProviderRequestError,
    ProviderResponseError,
    VocabularyProvider,
    build_vocabulary_provider,
)
from .schemas import (
    VocabularyExtractionCandidate,
    VocabularyExtractionResponse,
    VocabularyItemResponse,
)
from .source_acquisition import SourceAcquisitionError, acquire_automatic_source
from .source_cache import (
    CURRENT_SUBTITLE_CACHE_VERSION,
    SubtitleCacheError,
    lookup_owned_subtitle_cache,
    store_owned_subtitle_cache,
)
from .subtitle_filter import (
    DEFAULT_MAX_CHARACTERS,
    DEFAULT_MAX_WORDS,
    SubtitleFilterConfigurationError,
    filter_subtitle_document,
)
from .text import BlankSentenceError, derive_blank_sentence, validate_blank_sentence


logger = logging.getLogger(__name__)
usage_logger = logging.getLogger("vocabulary.usage")


class VocabularyGenerationError(Exception):
    """Base exception whose message is safe to show to an end user."""


class VocabularyConfigurationError(VocabularyGenerationError):
    pass


class VocabularyProviderError(VocabularyGenerationError):
    pass


class VocabularyResponseError(VocabularyGenerationError):
    pass


class VocabularyPersistenceError(VocabularyGenerationError):
    pass


@dataclass(frozen=True, slots=True)
class VocabularyGenerationResult:
    movie: Movie
    created_count: int
    skipped_count: int
    movie_created: bool


@dataclass(frozen=True, slots=True)
class PreparedVocabularySource:
    source: SourceDocument | None
    movie: Movie | None
    note: str | None
    cache_hit: bool = False


@dataclass(frozen=True, slots=True)
class CandidateRejections:
    duplicate: int = 0
    ungrounded: int = 0
    invalid_example: int = 0

    @property
    def total(self) -> int:
        return self.duplicate + self.ungrounded + self.invalid_example


def _positive_filter_limit(setting_name: str, default: int) -> int:
    value = getattr(settings, setting_name, default)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise SubtitleFilterConfigurationError(
            "Subtitle filtering is not configured correctly."
        )
    return value


def _filter_source(document: SourceDocument) -> SourceDocument:
    return filter_subtitle_document(
        document,
        max_words=_positive_filter_limit(
            "VOCABULARY_FILTER_MAX_WORDS",
            DEFAULT_MAX_WORDS,
        ),
        max_characters=_positive_filter_limit(
            "VOCABULARY_FILTER_MAX_CHARACTERS",
            DEFAULT_MAX_CHARACTERS,
        ),
    )


def prepare_vocabulary_source(
    *,
    user: Any,
    title: str,
    release_year: int | None,
    uploaded_source: SourceDocument | None = None,
) -> PreparedVocabularySource:
    """Prepare bounded source context, consulting the owned cache before HTTP."""
    if uploaded_source is not None:
        try:
            filtered_source = _filter_source(uploaded_source)
        except (SubtitleFilterConfigurationError, TypeError, ValueError):
            logger.exception("Uploaded vocabulary source could not be filtered")
            return PreparedVocabularySource(
                source=None,
                movie=None,
                note=(
                    "The uploaded source could not be pre-filtered. "
                    "Generated from model knowledge instead."
                ),
            )
        if filtered_source.text:
            return PreparedVocabularySource(
                source=filtered_source,
                movie=None,
                note=(
                    f"Source: uploaded {uploaded_source.format.upper()} file, "
                    "pre-filtered locally."
                ),
            )
        return PreparedVocabularySource(
            source=None,
            movie=None,
            note=(
                "The uploaded source contained no locally recognized B2-C2 "
                "candidates. Generated from model knowledge instead."
            ),
        )

    try:
        cached = lookup_owned_subtitle_cache(
            user=user,
            title=title,
            release_year=release_year,
            cache_version=CURRENT_SUBTITLE_CACHE_VERSION,
        )
    except SubtitleCacheError:
        logger.exception("Owned subtitle cache lookup failed")
        cached = None

    if cached is not None and cached.cache_hit:
        if cached.document is not None:
            return PreparedVocabularySource(
                source=cached.document,
                movie=cached.movie,
                note="Source: locally cached, pre-filtered English subtitles.",
                cache_hit=True,
            )
        return PreparedVocabularySource(
            source=None,
            movie=cached.movie,
            note=(
                "Cached subtitles contained no locally recognized B2-C2 "
                "candidates. Generated from model knowledge instead."
            ),
            cache_hit=True,
        )

    cached_movie = cached.movie if cached is not None else None
    try:
        acquired = acquire_automatic_source(
            title=title,
            release_year=release_year,
            imdb_id=cached_movie.imdb_id if cached_movie is not None else None,
        )
    except SourceAcquisitionError as exc:
        return PreparedVocabularySource(
            source=None,
            movie=cached_movie,
            note=f"{exc} Generated from model knowledge instead.",
        )

    if acquired is None:
        return PreparedVocabularySource(
            source=None,
            movie=cached_movie,
            note=None,
        )

    try:
        filtered_source = _filter_source(acquired.document)
        stored = store_owned_subtitle_cache(
            user=user,
            title=title,
            release_year=release_year,
            filtered_text=filtered_source.text,
            imdb_id=acquired.imdb_id,
            cache_version=CURRENT_SUBTITLE_CACHE_VERSION,
        )
    except (SubtitleCacheError, SubtitleFilterConfigurationError, TypeError, ValueError):
        logger.exception("Acquired subtitle source could not be filtered or cached")
        return PreparedVocabularySource(
            source=None,
            movie=cached_movie,
            note=(
                "Automatic subtitles could not be pre-filtered safely. "
                "Generated from model knowledge instead."
            ),
        )

    if stored.document is None:
        return PreparedVocabularySource(
            source=None,
            movie=stored.movie,
            note=(
                "Automatically matched subtitles contained no locally recognized "
                "B2-C2 candidates. Generated from model knowledge instead."
            ),
        )
    return PreparedVocabularySource(
        source=stored.document,
        movie=stored.movie,
        note=(
            f"Source: automatically matched English subtitles from "
            f"{acquired.provider}, pre-filtered and cached locally."
        ),
    )


def _normalise_title(value: str) -> str:
    return " ".join(value.split()).casefold()


def _request_candidates(
    *,
    movie: Movie,
    item_count: int,
    provider: VocabularyProvider,
    source: SourceDocument | None,
) -> VocabularyExtractionCandidate:
    movie_label = movie.title
    if movie.release_year is not None:
        movie_label = f"{movie.title} ({movie.release_year})"

    try:
        parsed = provider.generate(
            movie_title=movie.title,
            movie_reference=movie_label,
            item_count=item_count,
            source=source,
        )
    except ProviderConfigurationError as exc:
        raise VocabularyConfigurationError(str(exc)) from exc
    except ProviderResponseError as exc:
        raise VocabularyResponseError(
            "The generated vocabulary could not be validated. Please try again."
        ) from exc
    except ProviderRequestError as exc:
        raise VocabularyProviderError(
            "Vocabulary generation is temporarily unavailable. Please try again later."
        ) from exc

    if parsed is None:
        raise VocabularyResponseError(
            "The vocabulary request could not be completed. Please try a different movie."
        )

    try:
        if hasattr(parsed, "model_dump"):
            parsed = parsed.model_dump(mode="json", by_alias=True)
        parsed = VocabularyExtractionCandidate.model_validate(parsed)
    except PydanticValidationError as exc:
        logger.warning(
            "%s returned invalid parsed vocabulary data",
            provider.name,
            exc_info=True,
        )
        raise VocabularyResponseError(
            "The generated vocabulary could not be validated. Please try again."
        ) from exc

    if _normalise_title(parsed.movie_title) != _normalise_title(movie.title):
        raise VocabularyResponseError(
            "The generated vocabulary did not match the selected movie. Please try again."
        )
    if len(parsed.items) > item_count:
        logger.warning(
            "%s returned %d candidates above the request budget; extras were trimmed",
            provider.name,
            len(parsed.items) - item_count,
        )
        parsed = VocabularyExtractionCandidate.model_validate(
            {
                "movie_title": parsed.movie_title,
                "items": [
                    item.model_dump(mode="json", by_alias=True)
                    for item in parsed.items[:item_count]
                ],
            }
        )

    return parsed


def _accept_candidates(
    payload: VocabularyExtractionCandidate,
    *,
    source: SourceDocument | None,
    seen_terms: set[str],
) -> tuple[list[VocabularyItemResponse], CandidateRejections]:
    accepted: list[VocabularyItemResponse] = []
    duplicate_count = 0
    ungrounded_count = 0
    invalid_example_count = 0

    for candidate in payload.items:
        term_key = _normalise_title(candidate.word_or_phrase)
        if term_key in seen_terms:
            duplicate_count += 1
            continue
        if source is not None and not source_contains_term(
            source.text, candidate.word_or_phrase
        ):
            ungrounded_count += 1
            continue

        try:
            blank_sentence = derive_blank_sentence(
                candidate.word_or_phrase,
                candidate.example_sentence,
            )
            item_data = candidate.model_dump(
                mode="json",
                by_alias=True,
                exclude={"blank_sentence"},
            )
            item_data["blank_sentence"] = blank_sentence
            item = VocabularyItemResponse.model_validate(item_data)
        except (BlankSentenceError, PydanticValidationError):
            invalid_example_count += 1
            continue

        accepted.append(item)
        seen_terms.add(term_key)

    return accepted, CandidateRejections(
        duplicate=duplicate_count,
        ungrounded=ungrounded_count,
        invalid_example=invalid_example_count,
    )


def _request_payload(
    *,
    movie: Movie,
    item_count: int,
    provider: VocabularyProvider,
    source: SourceDocument | None,
) -> VocabularyExtractionResponse:
    candidate_count = item_count + GENERATION_CANDIDATE_SURPLUS
    first_payload = _request_candidates(
        movie=movie,
        item_count=candidate_count,
        provider=provider,
        source=source,
    )
    seen_terms: set[str] = set()
    accepted, initial_rejections = _accept_candidates(
        first_payload,
        source=source,
        seen_terms=seen_terms,
    )
    initial_returned = len(first_payload.items)
    initial_accepted = len(accepted)
    initial_rejected = initial_rejections.total

    if initial_rejected:
        logger.warning(
            "%s returned %d unusable vocabulary candidates; valid entries were kept",
            provider.name,
            initial_rejected,
        )

    usage_logger.info(
        "Vocabulary candidate yield: provider=%s requested_items=%d "
        "initial_requested=%d initial_returned=%d initial_accepted=%d "
        "initial_rejected=%d final_accepted=%d "
        "rejection_reasons=duplicate:%d,ungrounded:%d,invalid_example:%d",
        provider.name,
        item_count,
        candidate_count,
        initial_returned,
        initial_accepted,
        initial_rejected,
        min(len(accepted), item_count),
        initial_rejections.duplicate,
        initial_rejections.ungrounded,
        initial_rejections.invalid_example,
    )

    if not accepted:
        raise VocabularyResponseError(
            "The generated vocabulary could not be validated. Please try again."
        )

    return VocabularyExtractionResponse(
        movie_title=movie.title,
        items=accepted[:item_count],
    )


def _persist_payload(
    *,
    movie: Movie,
    payload: VocabularyExtractionResponse,
) -> VocabularyGenerationResult:
    with transaction.atomic():
        movie_created = False
        if movie.pk is not None:
            locked_movie = Movie.objects.select_for_update().get(pk=movie.pk)
        else:
            locked_movie = (
                Movie.objects.select_for_update()
                .filter(
                    user=movie.user,
                    title__iexact=movie.title,
                    release_year=movie.release_year,
                )
                .order_by("pk")
                .first()
            )
            if locked_movie is None:
                try:
                    # The savepoint lets us recover if another request creates the
                    # same user/title/year after the ownership lookup.
                    with transaction.atomic():
                        locked_movie = Movie.objects.create(
                            user=movie.user,
                            title=movie.title,
                            release_year=movie.release_year,
                        )
                    movie_created = True
                except IntegrityError:
                    locked_movie = Movie.objects.select_for_update().get(
                        user=movie.user,
                        title=movie.title,
                        release_year=movie.release_year,
                    )
        existing_terms = {
            term.strip().casefold()
            for term in locked_movie.vocabulary_items.values_list(
                "word_or_phrase", flat=True
            )
        }
        pending: list[VocabularyItem] = []
        skipped_count = 0

        for generated in payload.items:
            term_key = generated.word_or_phrase.casefold()
            if term_key in existing_terms:
                skipped_count += 1
                continue

            try:
                blank_sentence = validate_blank_sentence(
                    generated.word_or_phrase,
                    generated.example_sentence,
                    generated.blank_sentence,
                )
            except BlankSentenceError as exc:
                raise VocabularyResponseError(
                    "The generated quiz sentences could not be validated. Please try again."
                ) from exc

            vocabulary_item = VocabularyItem(
                movie=locked_movie,
                word_or_phrase=generated.word_or_phrase,
                type=generated.type.value,
                cefr_level=generated.cefr_level.value,
                definition_en=generated.definition_en,
                example_sentence=generated.example_sentence,
                blank_sentence=blank_sentence,
            )
            vocabulary_item.full_clean()
            pending.append(vocabulary_item)
            existing_terms.add(term_key)

        VocabularyItem.objects.bulk_create(pending)
        return VocabularyGenerationResult(
            movie=locked_movie,
            created_count=len(pending),
            skipped_count=skipped_count,
            movie_created=movie_created,
        )


def generate_and_save_vocabulary(
    *,
    movie: Movie | None = None,
    user: Any | None = None,
    title: str | None = None,
    release_year: int | None = None,
    item_count: int = 12,
    client: Any | None = None,
    source: SourceDocument | None = None,
) -> VocabularyGenerationResult:
    """Generate, strictly validate, and atomically persist movie vocabulary."""
    if (
        not isinstance(item_count, int)
        or isinstance(item_count, bool)
        or not 1 <= item_count <= MAX_GENERATION_ITEMS
    ):
        raise ValueError(f"item_count must be between 1 and {MAX_GENERATION_ITEMS}.")
    if source is not None and (
        not isinstance(source, SourceDocument) or not source.text.strip()
    ):
        raise ValueError("source must be a non-empty SourceDocument.")

    if movie is not None:
        if user is not None or title is not None or release_year is not None:
            raise ValueError("Pass either movie or user/title fields, not both.")
        generation_movie = movie
    else:
        if user is None or not getattr(user, "is_authenticated", False):
            raise ValueError("An authenticated user is required.")
        cleaned_title = " ".join((title or "").split())
        if not cleaned_title:
            raise ValueError("A movie title is required.")
        generation_movie = Movie(
            user=user,
            title=cleaned_title,
            release_year=release_year,
        )
        try:
            generation_movie.clean_fields()
        except DjangoValidationError as exc:
            raise ValueError("The movie details are invalid.") from exc

    try:
        provider = build_vocabulary_provider(client=client)
    except ProviderConfigurationError as exc:
        raise VocabularyConfigurationError(str(exc)) from exc
    try:
        payload = _request_payload(
            movie=generation_movie,
            item_count=item_count,
            provider=provider,
            source=source,
        )
    finally:
        provider.close()
    try:
        return _persist_payload(movie=generation_movie, payload=payload)
    except VocabularyGenerationError:
        raise
    except (DjangoValidationError, IntegrityError, Movie.DoesNotExist, ValueError) as exc:
        logger.exception("Validated vocabulary could not be persisted")
        raise VocabularyPersistenceError(
            "The vocabulary could not be saved. Please try again."
        ) from exc
