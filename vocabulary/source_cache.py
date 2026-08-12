from dataclasses import dataclass
from typing import Any

from django.core.exceptions import ValidationError as DjangoValidationError
from django.db import IntegrityError, transaction

from movies.models import Movie, current_year

from .ingestion import SourceDocument


# Bump when filter rules or the meaning/size of the cached source envelope changes.
CURRENT_SUBTITLE_CACHE_VERSION = 3
MAX_SUBTITLE_CACHE_VERSION = 32767


class SubtitleCacheError(ValueError):
    pass


class SubtitleCacheConflictError(SubtitleCacheError):
    pass


@dataclass(frozen=True, slots=True)
class SubtitleCacheLookup:
    movie: Movie | None
    cache_hit: bool
    document: SourceDocument | None
    movie_created: bool = False
    cache_written: bool = False

    @property
    def negative_hit(self) -> bool:
        return self.cache_hit and self.document is None

    @property
    def stale(self) -> bool:
        return bool(
            self.movie is not None
            and self.movie.filtered_subtitle_text is not None
            and not self.cache_hit
        )


def normalise_imdb_id(value: str | int | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        raise SubtitleCacheError("IMDb ID must be a number or digit string.")

    candidate = str(value).strip()
    if candidate.casefold().startswith("tt"):
        candidate = candidate[2:]
    if not candidate.isascii() or not candidate.isdigit():
        raise SubtitleCacheError("IMDb ID must contain digits only.")

    canonical = str(int(candidate))
    if canonical == "0" or len(canonical) > 10:
        raise SubtitleCacheError("IMDb ID is outside the supported range.")
    return canonical


def build_source_document_from_cache(
    movie: Movie,
    *,
    cache_version: int = CURRENT_SUBTITLE_CACHE_VERSION,
) -> SourceDocument | None:
    version = _validate_cache_version(cache_version)
    if (
        movie.filtered_subtitle_text is None
        or movie.subtitle_cache_version != version
    ):
        return None

    text = movie.filtered_subtitle_text.strip()
    if not text:
        return None
    return SourceDocument(text=text, format="script", pre_filtered=True)


def lookup_owned_subtitle_cache(
    *,
    user: Any,
    title: str,
    release_year: int | None,
    cache_version: int = CURRENT_SUBTITLE_CACHE_VERSION,
) -> SubtitleCacheLookup:
    user_id = _authenticated_user_id(user)
    cleaned_title = _clean_title(title)
    cleaned_year = _validate_release_year(release_year)
    version = _validate_cache_version(cache_version)
    movie = _owned_movie_query(
        user_id=user_id,
        title=cleaned_title,
        release_year=cleaned_year,
    ).first()
    return _build_lookup(movie, cache_version=version)


def store_owned_subtitle_cache(
    *,
    user: Any,
    title: str,
    release_year: int | None,
    filtered_text: str,
    imdb_id: str | int | None,
    cache_version: int = CURRENT_SUBTITLE_CACHE_VERSION,
) -> SubtitleCacheLookup:
    """Store a versioned result without replacing a winner from the same version.

    An empty string is a valid negative cache entry. Callers should only store one
    for a deterministic no-candidates result, never for a transient network error.
    """

    user_id = _authenticated_user_id(user)
    cleaned_title = _clean_title(title)
    cleaned_year = _validate_release_year(release_year)
    version = _validate_cache_version(cache_version)
    canonical_imdb_id = normalise_imdb_id(imdb_id)
    if not isinstance(filtered_text, str):
        raise SubtitleCacheError("Filtered subtitle text must be a string.")
    cleaned_text = filtered_text.strip()

    with transaction.atomic():
        movie = (
            _owned_movie_query(
                user_id=user_id,
                title=cleaned_title,
                release_year=cleaned_year,
            )
            .select_for_update()
            .first()
        )
        movie_created = False
        if movie is None:
            try:
                with transaction.atomic():
                    movie = Movie(
                        user_id=user_id,
                        title=cleaned_title,
                        release_year=cleaned_year,
                    )
                    movie.full_clean(
                        validate_unique=False,
                        validate_constraints=False,
                    )
                    movie.save(force_insert=True)
                movie_created = True
            except (DjangoValidationError, IntegrityError) as exc:
                movie = (
                    _owned_movie_query(
                        user_id=user_id,
                        title=cleaned_title,
                        release_year=cleaned_year,
                    )
                    .select_for_update()
                    .first()
                )
                if movie is None:
                    raise SubtitleCacheError(
                        "The subtitle cache movie could not be created."
                    ) from exc

        _reject_imdb_conflict(movie, canonical_imdb_id)
        changed_fields: list[str] = []
        if canonical_imdb_id is not None and movie.imdb_id is None:
            movie.imdb_id = canonical_imdb_id
            changed_fields.append("imdb_id")

        cache_written = not _is_current_cache(movie, version)
        if cache_written:
            movie.filtered_subtitle_text = cleaned_text
            movie.subtitle_cache_version = version
            changed_fields.extend(
                ["filtered_subtitle_text", "subtitle_cache_version"]
            )

        if changed_fields:
            movie.save(update_fields=changed_fields)

        return _build_lookup(
            movie,
            cache_version=version,
            movie_created=movie_created,
            cache_written=cache_written,
        )


def _build_lookup(
    movie: Movie | None,
    *,
    cache_version: int,
    movie_created: bool = False,
    cache_written: bool = False,
) -> SubtitleCacheLookup:
    cache_hit = bool(movie is not None and _is_current_cache(movie, cache_version))
    document = (
        build_source_document_from_cache(movie, cache_version=cache_version)
        if cache_hit and movie is not None
        else None
    )
    return SubtitleCacheLookup(
        movie=movie,
        cache_hit=cache_hit,
        document=document,
        movie_created=movie_created,
        cache_written=cache_written,
    )


def _owned_movie_query(*, user_id: int, title: str, release_year: int | None):
    return Movie.objects.filter(
        user_id=user_id,
        title__iexact=title,
        release_year=release_year,
    ).order_by("pk")


def _is_current_cache(movie: Movie, cache_version: int) -> bool:
    return (
        movie.filtered_subtitle_text is not None
        and movie.subtitle_cache_version == cache_version
    )


def _reject_imdb_conflict(movie: Movie, imdb_id: str | None) -> None:
    if imdb_id is None or movie.imdb_id is None:
        return
    if normalise_imdb_id(movie.imdb_id) != imdb_id:
        raise SubtitleCacheConflictError(
            "The resolved IMDb ID does not match the cached movie."
        )


def _authenticated_user_id(user: Any) -> int:
    user_id = getattr(user, "pk", None)
    if not getattr(user, "is_authenticated", False) or user_id is None:
        raise SubtitleCacheError("An authenticated user is required.")
    return user_id


def _clean_title(title: str) -> str:
    if not isinstance(title, str):
        raise SubtitleCacheError("A movie title is required.")
    cleaned_title = " ".join(title.split())
    if not cleaned_title or len(cleaned_title) > 255:
        raise SubtitleCacheError("A valid movie title is required.")
    return cleaned_title


def _validate_release_year(release_year: int | None) -> int | None:
    if release_year is None:
        return None
    if (
        isinstance(release_year, bool)
        or not isinstance(release_year, int)
        or not 1888 <= release_year <= current_year()
    ):
        raise SubtitleCacheError("A valid movie release year is required.")
    return release_year


def _validate_cache_version(cache_version: int) -> int:
    if (
        isinstance(cache_version, bool)
        or not isinstance(cache_version, int)
        or not 1 <= cache_version <= MAX_SUBTITLE_CACHE_VERSION
    ):
        raise SubtitleCacheError("A valid subtitle cache version is required.")
    return cache_version
