import logging
import math
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from django.conf import settings
from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction
from pydantic import ValidationError as PydanticValidationError

from movies.models import Movie, current_year

from .constants import (
    GENERATION_CANDIDATE_SURPLUS_RATIO,
    MAX_GENERATION_CANDIDATES,
    MAX_GENERATION_CANDIDATE_SURPLUS,
    MAX_GENERATION_ITEMS,
    MIN_GENERATION_CANDIDATE_SURPLUS,
)
from .ingestion import SourceDocument
from .matching import source_contains_term
from .models import VocabularyItem
from .providers import (
    SYSTEM_PROMPT,
    CandidateSchemaRejections,
    ProviderConfigurationError,
    ProviderRequestError,
    ProviderResponseError,
    VocabularyLLMClient,
    VocabularyProviderResult,
    build_vocabulary_llm_client,
)
from .schemas import (
    VocabularyExtractionCandidate,
    VocabularyExtractionResponse,
    VocabularyItemCandidate,
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
    subtitle_filter_budget,
)
from .text import (
    BlankSentenceError,
    BlankSentenceErrorReason,
    derive_blank_sentence,
    validate_blank_sentence,
)


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
class PreparedVocabularySource:
    source: SourceDocument | None
    movie: Movie | None
    note: str | None
    cache_hit: bool = False


@dataclass(frozen=True, slots=True)
class CandidateRejections:
    duplicate: int = 0
    ungrounded: int = 0
    malformed: int = 0

    @property
    def total(self) -> int:
        return self.duplicate + self.ungrounded + self.malformed


@dataclass(frozen=True, slots=True)
class ClozeIneligibility:
    missing_target: int = 0
    ambiguous_target: int = 0
    preexisting_blank: int = 0
    missing_text: int = 0
    other: int = 0

    @property
    def total(self) -> int:
        return sum(
            (
                self.missing_target,
                self.ambiguous_target,
                self.preexisting_blank,
                self.missing_text,
                self.other,
            )
        )


class VocabularyYieldReason(StrEnum):
    PROVIDER_SHORTFALL = "provider_shortfall"
    GENERATED_DUPLICATE = "generated_duplicate"
    UNGROUNDED = "ungrounded"
    INVALID_SCHEMA = "invalid_schema"
    ALREADY_SAVED = "already_saved"
    OTHER = "other"


@dataclass(frozen=True, slots=True)
class CandidateYield:
    provider_returned_count: int
    validated_candidate_count: int
    rejections: CandidateRejections
    schema_rejections: CandidateSchemaRejections
    cloze_ineligibility: ClozeIneligibility


@dataclass(frozen=True, slots=True)
class _RequestedCandidates:
    movie_title: str
    items: tuple[VocabularyItemCandidate, ...]
    returned_count: int
    schema_rejections: CandidateSchemaRejections
    trimmed_count: int = 0
    editorial_filtered_count: int = 0
    extraction_returned_count: int | None = None


@dataclass(frozen=True, slots=True)
class VocabularyPromptBenchmarkResult:
    movie_title: str
    candidate_limit: int
    provider_name: str
    provider_returned_count: int
    schema_valid_count: int
    items: tuple[VocabularyItemResponse, ...]
    rejections: CandidateRejections
    schema_rejections: CandidateSchemaRejections
    cloze_ineligibility: ClozeIneligibility
    over_limit_count: int = 0
    release_year: int | None = None
    editorial_filtered_count: int = 0
    extraction_returned_count: int | None = None

    @property
    def accepted_count(self) -> int:
        return len(self.items)

    @property
    def rejected_count(self) -> int:
        return (
            self.rejections.duplicate
            + self.rejections.ungrounded
            + self.schema_rejections.total
            + self.over_limit_count
            + self.editorial_filtered_count
        )


@dataclass(frozen=True, slots=True)
class VocabularyGenerationResult:
    movie: Movie
    created_count: int
    skipped_count: int
    movie_created: bool
    requested_count: int | None = None
    provider_returned_count: int | None = None
    validated_candidate_count: int | None = None
    candidate_rejections: CandidateRejections = field(
        default_factory=CandidateRejections
    )
    schema_rejections: CandidateSchemaRejections = field(
        default_factory=CandidateSchemaRejections
    )
    cloze_ineligibility: ClozeIneligibility = field(
        default_factory=ClozeIneligibility
    )

    @property
    def has_shortfall(self) -> bool:
        return bool(
            self.requested_count is not None
            and self.created_count < self.requested_count
        )

    @property
    def yield_reasons(self) -> tuple[VocabularyYieldReason, ...]:
        if not self.has_shortfall or self.requested_count is None:
            return ()

        reasons: list[VocabularyYieldReason] = []
        if (
            self.provider_returned_count is not None
            and self.provider_returned_count < self.requested_count
        ):
            reasons.append(VocabularyYieldReason.PROVIDER_SHORTFALL)
        validation_caused_shortfall = (
            self.validated_candidate_count is not None
            and self.validated_candidate_count < self.requested_count
        )
        if validation_caused_shortfall:
            if self.candidate_rejections.duplicate:
                reasons.append(VocabularyYieldReason.GENERATED_DUPLICATE)
            if self.candidate_rejections.ungrounded:
                reasons.append(VocabularyYieldReason.UNGROUNDED)
            if self.candidate_rejections.malformed:
                reasons.append(VocabularyYieldReason.INVALID_SCHEMA)
        if self.skipped_count:
            reasons.append(VocabularyYieldReason.ALREADY_SAVED)
        if not reasons:
            reasons.append(VocabularyYieldReason.OTHER)
        return tuple(reasons)


def _positive_filter_limit(setting_name: str, default: int) -> int:
    value = getattr(settings, setting_name, default)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise SubtitleFilterConfigurationError(
            "Subtitle filtering is not configured correctly."
        )
    return value


def _filter_source(
    document: SourceDocument,
    *,
    item_count: int,
) -> SourceDocument:
    budget = subtitle_filter_budget(
        item_count,
        base_max_words=_positive_filter_limit(
            "VOCABULARY_FILTER_MAX_WORDS",
            DEFAULT_MAX_WORDS,
        ),
        base_max_characters=_positive_filter_limit(
            "VOCABULARY_FILTER_MAX_CHARACTERS",
            DEFAULT_MAX_CHARACTERS,
        ),
    )
    return filter_subtitle_document(
        document,
        max_words=budget.max_words,
        max_characters=budget.max_characters,
    )


def prepare_benchmark_source(
    document: SourceDocument,
    *,
    candidate_limit: int,
) -> SourceDocument:
    """Apply the production source filter without consulting cache or database."""
    if not isinstance(document, SourceDocument) or not document.text.strip():
        raise ValueError("source must be a non-empty SourceDocument.")
    if (
        not isinstance(candidate_limit, int)
        or isinstance(candidate_limit, bool)
        or not 1 <= candidate_limit <= MAX_GENERATION_CANDIDATES
    ):
        raise ValueError(
            f"candidate_limit must be between 1 and {MAX_GENERATION_CANDIDATES}."
        )
    return _filter_source(
        document,
        item_count=min(candidate_limit, MAX_GENERATION_ITEMS),
    )


def _bound_cached_source(
    document: SourceDocument,
    *,
    item_count: int,
) -> SourceDocument:
    if item_count == MAX_GENERATION_ITEMS:
        return document
    return _filter_source(document, item_count=item_count)


def prepare_vocabulary_source(
    *,
    user: Any,
    title: str,
    release_year: int | None,
    item_count: int = 12,
    uploaded_source: SourceDocument | None = None,
) -> PreparedVocabularySource:
    """Prepare bounded source context, consulting the owned cache before HTTP."""
    if uploaded_source is not None:
        try:
            filtered_source = _filter_source(
                uploaded_source,
                item_count=item_count,
            )
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
                "The uploaded source contained no locally recognized B1-C2 "
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
            try:
                bounded_source = _bound_cached_source(
                    cached.document,
                    item_count=item_count,
                )
            except (SubtitleFilterConfigurationError, TypeError, ValueError):
                logger.exception("Cached vocabulary source could not be bounded")
                return PreparedVocabularySource(
                    source=None,
                    movie=cached.movie,
                    note=(
                        "Cached subtitles could not be bounded safely. "
                        "Generated from model knowledge instead."
                    ),
                    cache_hit=True,
                )
            else:
                return PreparedVocabularySource(
                    source=bounded_source if bounded_source.text else None,
                    movie=cached.movie,
                    note="Source: locally cached, pre-filtered English subtitles.",
                    cache_hit=True,
                )
        return PreparedVocabularySource(
            source=None,
            movie=cached.movie,
            note=(
                "Cached subtitles contained no locally recognized B1-C2 "
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
        cache_source = _filter_source(
            acquired.document,
            item_count=MAX_GENERATION_ITEMS,
        )
        stored = store_owned_subtitle_cache(
            user=user,
            title=title,
            release_year=release_year,
            filtered_text=cache_source.text,
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
                "B1-C2 candidates. Generated from model knowledge instead."
            ),
        )
    try:
        bounded_source = _bound_cached_source(
            stored.document,
            item_count=item_count,
        )
    except (SubtitleFilterConfigurationError, TypeError, ValueError):
        logger.exception("Stored vocabulary source could not be bounded")
        return PreparedVocabularySource(
            source=None,
            movie=stored.movie,
            note=(
                "Automatic subtitles could not be bounded safely. "
                "Generated from model knowledge instead."
            ),
        )
    return PreparedVocabularySource(
        source=bounded_source if bounded_source.text else None,
        movie=stored.movie,
        note=(
            f"Source: automatically matched English subtitles from "
            f"{acquired.provider}, pre-filtered and cached locally."
        ),
    )


def _normalise_title(value: str) -> str:
    return " ".join(value.split()).casefold()


def _request_candidates_for_reference(
    *,
    movie_title: str,
    movie_reference: str,
    candidate_limit: int,
    provider: VocabularyLLMClient,
    source: SourceDocument | None,
) -> _RequestedCandidates:
    try:
        parsed = provider.generate(
            movie_title=movie_title,
            movie_reference=movie_reference,
            candidate_limit=candidate_limit,
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

    if isinstance(parsed, VocabularyProviderResult):
        requested = _RequestedCandidates(
            movie_title=parsed.movie_title,
            items=parsed.items,
            returned_count=parsed.returned_count,
            schema_rejections=parsed.schema_rejections,
            editorial_filtered_count=parsed.editorial_filtered_count,
            extraction_returned_count=parsed.extraction_returned_count,
        )
    else:
        try:
            if hasattr(parsed, "model_dump"):
                parsed = parsed.model_dump(mode="json", by_alias=True)
            validated = VocabularyExtractionCandidate.model_validate(parsed)
        except PydanticValidationError as exc:
            logger.warning(
                "%s returned invalid parsed vocabulary data",
                provider.name,
                exc_info=True,
            )
            raise VocabularyResponseError(
                "The generated vocabulary could not be validated. Please try again."
            ) from exc
        requested = _RequestedCandidates(
            movie_title=validated.movie_title,
            items=tuple(validated.items),
            returned_count=len(validated.items),
            schema_rejections=CandidateSchemaRejections(),
        )

    if _normalise_title(requested.movie_title) != _normalise_title(movie_title):
        raise VocabularyResponseError(
            "The generated vocabulary did not match the selected movie. Please try again."
        )
    if len(requested.items) > candidate_limit:
        trimmed_count = len(requested.items) - candidate_limit
        logger.warning(
            "%s returned %d candidates above the request budget; extras were trimmed",
            provider.name,
            trimmed_count,
        )
        requested = _RequestedCandidates(
            movie_title=requested.movie_title,
            items=requested.items[:candidate_limit],
            returned_count=requested.returned_count,
            schema_rejections=requested.schema_rejections,
            trimmed_count=requested.trimmed_count + trimmed_count,
            editorial_filtered_count=requested.editorial_filtered_count,
            extraction_returned_count=requested.extraction_returned_count,
        )

    return requested


def _request_candidates(
    *,
    movie: Movie,
    candidate_limit: int,
    provider: VocabularyLLMClient,
    source: SourceDocument | None,
) -> _RequestedCandidates:
    movie_reference = movie.title
    if movie.release_year is not None:
        movie_reference = f"{movie.title} ({movie.release_year})"
    return _request_candidates_for_reference(
        movie_title=movie.title,
        movie_reference=movie_reference,
        candidate_limit=candidate_limit,
        provider=provider,
        source=source,
    )


def _accept_candidates(
    payload: _RequestedCandidates,
    *,
    source: SourceDocument | None,
    seen_terms: set[str],
) -> tuple[
    list[VocabularyItemResponse],
    CandidateRejections,
    ClozeIneligibility,
]:
    accepted: list[VocabularyItemResponse] = []
    duplicate_count = 0
    ungrounded_count = 0
    cloze_counts = {
        "missing_target": 0,
        "ambiguous_target": 0,
        "preexisting_blank": 0,
        "missing_text": 0,
        "other": 0,
    }

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

        blank_sentence = None
        try:
            blank_sentence = derive_blank_sentence(
                candidate.word_or_phrase,
                candidate.example_sentence,
            )
        except BlankSentenceError as exc:
            cloze_category = {
                BlankSentenceErrorReason.MISSING_TARGET: "missing_target",
                BlankSentenceErrorReason.AMBIGUOUS_TARGET: "ambiguous_target",
                BlankSentenceErrorReason.PREEXISTING_BLANK: "preexisting_blank",
                BlankSentenceErrorReason.MISSING_TEXT: "missing_text",
            }.get(exc.reason, "other")
            cloze_counts[cloze_category] += 1

        item_data = candidate.model_dump(mode="json", by_alias=True)
        item_data["blank_sentence"] = blank_sentence
        item = VocabularyItemResponse.model_validate(item_data)

        accepted.append(item)
        seen_terms.add(term_key)

    return (
        accepted,
        CandidateRejections(
            duplicate=duplicate_count,
            ungrounded=ungrounded_count,
            malformed=payload.schema_rejections.total,
        ),
        ClozeIneligibility(**cloze_counts),
    )


def benchmark_vocabulary_prompt(
    *,
    movie_title: str,
    release_year: int | None = None,
    candidate_limit: int = 50,
    client: Any | None = None,
    source: SourceDocument | None = None,
) -> VocabularyPromptBenchmarkResult:
    """Run the production extraction and validation path without database writes."""
    if not isinstance(movie_title, str):
        raise ValueError("A movie title is required.")
    cleaned_title = " ".join(movie_title.split())
    if not cleaned_title or len(cleaned_title) > 255:
        raise ValueError("A movie title between 1 and 255 characters is required.")
    max_release_year = current_year()
    if release_year is not None and (
        not isinstance(release_year, int)
        or isinstance(release_year, bool)
        or not 1888 <= release_year <= max_release_year
    ):
        raise ValueError(
            f"release_year must be between 1888 and {max_release_year}."
        )
    if (
        not isinstance(candidate_limit, int)
        or isinstance(candidate_limit, bool)
        or not 1 <= candidate_limit <= MAX_GENERATION_CANDIDATES
    ):
        raise ValueError(
            f"candidate_limit must be between 1 and {MAX_GENERATION_CANDIDATES}."
        )
    if source is not None and (
        not isinstance(source, SourceDocument) or not source.text.strip()
    ):
        raise ValueError("source must be a non-empty SourceDocument.")

    try:
        provider = build_vocabulary_llm_client(client=client)
    except ProviderConfigurationError as exc:
        raise VocabularyConfigurationError(str(exc)) from exc
    try:
        requested = _request_candidates_for_reference(
            movie_title=cleaned_title,
            movie_reference=(
                f"{cleaned_title} ({release_year})"
                if release_year is not None
                else cleaned_title
            ),
            candidate_limit=candidate_limit,
            provider=provider,
            source=source,
        )
        accepted, rejections, cloze_ineligibility = _accept_candidates(
            requested,
            source=source,
            seen_terms=set(),
        )
        return VocabularyPromptBenchmarkResult(
            movie_title=cleaned_title,
            candidate_limit=candidate_limit,
            provider_name=provider.name,
            provider_returned_count=requested.returned_count,
            schema_valid_count=len(requested.items) + requested.trimmed_count,
            items=tuple(accepted),
            rejections=rejections,
            schema_rejections=requested.schema_rejections,
            cloze_ineligibility=cloze_ineligibility,
            over_limit_count=requested.trimmed_count,
            release_year=release_year,
            editorial_filtered_count=requested.editorial_filtered_count,
            extraction_returned_count=requested.extraction_returned_count,
        )
    finally:
        provider.close()


def _candidate_limit(item_count: int) -> int:
    surplus = min(
        MAX_GENERATION_CANDIDATE_SURPLUS,
        max(
            MIN_GENERATION_CANDIDATE_SURPLUS,
            math.ceil(item_count * GENERATION_CANDIDATE_SURPLUS_RATIO),
        ),
    )
    return item_count + surplus


def _request_payload(
    *,
    movie: Movie,
    item_count: int,
    provider: VocabularyLLMClient,
    source: SourceDocument | None,
) -> tuple[VocabularyExtractionResponse, CandidateYield]:
    candidate_limit = _candidate_limit(item_count)
    first_payload = _request_candidates(
        movie=movie,
        candidate_limit=candidate_limit,
        provider=provider,
        source=source,
    )
    seen_terms: set[str] = set()
    accepted, initial_rejections, cloze_ineligibility = _accept_candidates(
        first_payload,
        source=source,
        seen_terms=seen_terms,
    )
    initial_returned = first_payload.returned_count
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
        "candidate_limit=%d initial_returned=%d initial_accepted=%d "
        "initial_rejected=%d final_accepted=%d "
        "rejection_reasons=duplicate:%d,ungrounded:%d,malformed:%d "
        "schema_reasons=term:%d,type:%d,cefr:%d,definition:%d,example:%d,"
        "extra:%d,other:%d cloze_ineligible=%d "
        "cloze_reasons=missing:%d,ambiguous:%d,preexisting_blank:%d,"
        "missing_text:%d,other:%d",
        provider.name,
        item_count,
        candidate_limit,
        initial_returned,
        initial_accepted,
        initial_rejected,
        min(len(accepted), item_count),
        initial_rejections.duplicate,
        initial_rejections.ungrounded,
        initial_rejections.malformed,
        first_payload.schema_rejections.invalid_term,
        first_payload.schema_rejections.invalid_type,
        first_payload.schema_rejections.invalid_cefr,
        first_payload.schema_rejections.invalid_definition,
        first_payload.schema_rejections.invalid_example,
        first_payload.schema_rejections.extra_fields,
        first_payload.schema_rejections.other,
        cloze_ineligibility.total,
        cloze_ineligibility.missing_target,
        cloze_ineligibility.ambiguous_target,
        cloze_ineligibility.preexisting_blank,
        cloze_ineligibility.missing_text,
        cloze_ineligibility.other,
    )

    if not accepted:
        raise VocabularyResponseError(
            "The generated vocabulary could not be validated. Please try again."
        )

    return (
        VocabularyExtractionResponse(
            movie_title=movie.title,
            items=accepted[:item_count],
        ),
        CandidateYield(
            provider_returned_count=initial_returned,
            validated_candidate_count=len(accepted[:item_count]),
            rejections=initial_rejections,
            schema_rejections=first_payload.schema_rejections,
            cloze_ineligibility=cloze_ineligibility,
        ),
    )


def _persist_payload(
    *,
    movie: Movie,
    payload: VocabularyExtractionResponse,
    requested_count: int,
    candidate_yield: CandidateYield,
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

            blank_sentence = generated.blank_sentence
            if blank_sentence is not None:
                try:
                    blank_sentence = validate_blank_sentence(
                        generated.word_or_phrase,
                        generated.example_sentence,
                        blank_sentence,
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
            requested_count=requested_count,
            provider_returned_count=candidate_yield.provider_returned_count,
            validated_candidate_count=candidate_yield.validated_candidate_count,
            candidate_rejections=candidate_yield.rejections,
            schema_rejections=candidate_yield.schema_rejections,
            cloze_ineligibility=candidate_yield.cloze_ineligibility,
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
        provider = build_vocabulary_llm_client(client=client)
    except ProviderConfigurationError as exc:
        raise VocabularyConfigurationError(str(exc)) from exc
    try:
        payload, candidate_yield = _request_payload(
            movie=generation_movie,
            item_count=item_count,
            provider=provider,
            source=source,
        )
    finally:
        provider.close()
    try:
        return _persist_payload(
            movie=generation_movie,
            payload=payload,
            requested_count=item_count,
            candidate_yield=candidate_yield,
        )
    except VocabularyGenerationError:
        raise
    except (DjangoValidationError, IntegrityError, Movie.DoesNotExist, ValueError) as exc:
        logger.exception("Validated vocabulary could not be persisted")
        raise VocabularyPersistenceError(
            "The vocabulary could not be saved. Please try again."
        ) from exc
