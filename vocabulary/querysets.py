from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

from django.db.models import CharField, Exists, OuterRef, Q, QuerySet, Subquery, Value
from django.db.models.functions import Coalesce

from quizzes.models import UserWordStatus

from .models import VocabularyItem


_PAYLOAD_FIELDS = frozenset(
    {"q", "status", "word_type", "movie_id", "cefr_levels"}
)
_VALID_STATUSES = frozenset(
    {"", "saved", *(choice.value for choice in UserWordStatus.Status)}
)
_VALID_WORD_TYPES = frozenset(VocabularyItem.Type.values)
_CEFR_ORDER = {
    value: index for index, value in enumerate(VocabularyItem.CefrLevel.values)
}


@dataclass(frozen=True, slots=True)
class VocabularyFilterSpec:
    """Canonical, serializable filters for an owned vocabulary queryset."""

    q: str = ""
    status: str = ""
    word_type: str = ""
    movie_id: int | None = None
    cefr_levels: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.q, str):
            raise ValueError("The vocabulary search query must be a string.")
        if len(self.q) > 255 or self.q != " ".join(self.q.split()):
            raise ValueError("The vocabulary search query is not canonical.")
        if not isinstance(self.status, str) or self.status not in _VALID_STATUSES:
            raise ValueError("The vocabulary status filter is invalid.")
        if not isinstance(self.word_type, str) or (
            self.word_type and self.word_type not in _VALID_WORD_TYPES
        ):
            raise ValueError("The vocabulary type filter is invalid.")
        if self.movie_id is not None and (
            not isinstance(self.movie_id, int)
            or isinstance(self.movie_id, bool)
            or self.movie_id < 1
        ):
            raise ValueError("The vocabulary movie filter is invalid.")
        if not isinstance(self.cefr_levels, tuple) or any(
            not isinstance(value, str) or value not in _CEFR_ORDER
            for value in self.cefr_levels
        ):
            raise ValueError("The vocabulary CEFR filters are invalid.")
        canonical_levels = tuple(
            sorted(set(self.cefr_levels), key=_CEFR_ORDER.__getitem__)
        )
        if self.cefr_levels != canonical_levels:
            raise ValueError("The vocabulary CEFR filters are not canonical.")

    @classmethod
    def from_cleaned_data(
        cls,
        cleaned_data: Mapping[str, Any],
    ) -> "VocabularyFilterSpec":
        """Create a canonical spec from VocabularyExplorerFilterForm data."""

        try:
            movie = cleaned_data["movie"]
            cefr_levels = cleaned_data["cefr"]
            canonical_levels = tuple(
                sorted(set(cefr_levels), key=_CEFR_ORDER.__getitem__)
            )
            return cls(
                q=cleaned_data["q"],
                status=cleaned_data["status"],
                word_type=cleaned_data["type"],
                movie_id=movie.pk if movie is not None else None,
                cefr_levels=canonical_levels,
            )
        except (AttributeError, KeyError, TypeError) as exc:
            raise ValueError("Validated vocabulary filters are required.") from exc

    @classmethod
    def from_payload(cls, payload: Mapping[str, object]) -> "VocabularyFilterSpec":
        """Strictly deserialize the JSON-compatible output of ``as_payload``."""

        if not isinstance(payload, Mapping) or set(payload) != _PAYLOAD_FIELDS:
            raise ValueError("The vocabulary filter payload is invalid.")
        cefr_levels = payload["cefr_levels"]
        if not isinstance(cefr_levels, list):
            raise ValueError("The vocabulary filter payload is invalid.")
        return cls(
            q=payload["q"],
            status=payload["status"],
            word_type=payload["word_type"],
            movie_id=payload["movie_id"],
            cefr_levels=tuple(cefr_levels),
        )

    def as_payload(self) -> dict[str, object]:
        """Return a stable JSON-compatible representation of this spec."""

        return {
            "q": self.q,
            "status": self.status,
            "word_type": self.word_type,
            "movie_id": self.movie_id,
            "cefr_levels": list(self.cefr_levels),
        }

    def as_query_pairs(self) -> tuple[tuple[str, str], ...]:
        """Return only active filters using the Explorer's public query names."""

        pairs: list[tuple[str, str]] = []
        if self.q:
            pairs.append(("q", self.q))
        if self.status:
            pairs.append(("status", self.status))
        if self.word_type:
            pairs.append(("type", self.word_type))
        if self.movie_id is not None:
            pairs.append(("movie", str(self.movie_id)))
        pairs.extend(("cefr", level) for level in self.cefr_levels)
        return tuple(pairs)

    def as_query_string(self) -> str:
        return urlencode(self.as_query_pairs())


def owned_vocabulary_queryset(user) -> QuerySet[VocabularyItem]:
    """Return vocabulary owned by ``user`` with Explorer status annotations."""

    status_for_user = UserWordStatus.objects.filter(
        user=user,
        vocabulary_item_id=OuterRef("pk"),
    ).order_by()
    return (
        VocabularyItem.objects.filter(movie__user=user)
        .select_related("movie")
        .annotate(
            learning_status=Coalesce(
                Subquery(status_for_user.values("status")[:1]),
                Value(UserWordStatus.Status.NEW),
                output_field=CharField(),
            ),
            is_saved_for_user=Exists(status_for_user.filter(is_saved=True)),
        )
    )


def filter_vocabulary_queryset(
    queryset: QuerySet[VocabularyItem],
    spec: VocabularyFilterSpec,
) -> QuerySet[VocabularyItem]:
    """Apply a validated filter spec without changing queryset ordering."""

    if not isinstance(spec, VocabularyFilterSpec):
        raise TypeError("A VocabularyFilterSpec is required.")

    if spec.q:
        queryset = queryset.filter(
            Q(word_or_phrase__icontains=spec.q)
            | Q(definition_en__icontains=spec.q)
            | Q(example_sentence__icontains=spec.q)
        )

    if spec.status == "saved":
        queryset = queryset.filter(is_saved_for_user=True)
    elif spec.status:
        queryset = queryset.filter(learning_status=spec.status)

    if spec.word_type:
        queryset = queryset.filter(type=spec.word_type)
    if spec.movie_id is not None:
        queryset = queryset.filter(movie_id=spec.movie_id)
    if spec.cefr_levels:
        queryset = queryset.filter(cefr_level__in=spec.cefr_levels)

    return queryset
