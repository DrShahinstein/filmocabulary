import json
import re
from dataclasses import dataclass
from functools import lru_cache
from importlib.resources import files
from typing import Literal

from .ingestion import SourceDocument


CEFRLevel = Literal["B1", "B2", "C1", "C2"]

FILTER_VERSION = "cefrj-1.6+octanove-c1c2-1.0:2"
DEFAULT_MAX_WORDS = 1_100
DEFAULT_MAX_CHARACTERS = 6_000
MAX_UTTERANCE_CHARACTERS = 420

_LEVEL_WEIGHT: dict[CEFRLevel, int] = {"B1": 1, "B2": 2, "C1": 3, "C2": 4}
_WORD_RE = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?", re.UNICODE)
_TERMINAL_RE = re.compile(r"[.!?\u2026][\"'\]\)]*$")
_SPEAKER_RE = re.compile(r"^(?:-\s+|[A-Za-z][A-Za-z0-9 .'-]{0,24}:\s*)")
_APOSTROPHE_TRANSLATION = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u02bc": "'",
        "`": "'",
    }
)
_DASH_TRANSLATION = str.maketrans(
    {
        "-": " ",
        "\u2010": " ",
        "\u2011": " ",
        "\u2012": " ",
        "\u2013": " ",
        "\u2014": " ",
        "\u2212": " ",
    }
)
_PARTICLES = frozenset(
    {
        "about",
        "across",
        "after",
        "along",
        "around",
        "at",
        "away",
        "back",
        "by",
        "down",
        "for",
        "forward",
        "in",
        "into",
        "off",
        "on",
        "out",
        "over",
        "through",
        "to",
        "together",
        "up",
        "upon",
        "with",
    }
)
_IRREGULAR_LEMMAS: dict[str, tuple[str, ...]] = {
    "arisen": ("arise",),
    "arose": ("arise",),
    "borne": ("bear",),
    "bought": ("buy",),
    "brought": ("bring",),
    "caught": ("catch",),
    "dealt": ("deal",),
    "dug": ("dig",),
    "fled": ("flee",),
    "forbade": ("forbid",),
    "forbidden": ("forbid",),
    "forgave": ("forgive",),
    "forgiven": ("forgive",),
    "forsaken": ("forsake",),
    "forsook": ("forsake",),
    "fought": ("fight",),
    "found": ("find",),
    "froze": ("freeze",),
    "frozen": ("freeze",),
    "gave": ("give",),
    "given": ("give",),
    "gone": ("go",),
    "grew": ("grow",),
    "grown": ("grow",),
    "held": ("hold",),
    "hid": ("hide",),
    "hidden": ("hide",),
    "kept": ("keep",),
    "knew": ("know",),
    "known": ("know",),
    "laid": ("lay",),
    "led": ("lead",),
    "left": ("leave",),
    "lost": ("lose",),
    "meant": ("mean",),
    "misled": ("mislead",),
    "overcame": ("overcome",),
    "proven": ("prove",),
    "ran": ("run",),
    "ridden": ("ride",),
    "rose": ("rise",),
    "sank": ("sink",),
    "sought": ("seek",),
    "spoke": ("speak",),
    "spoken": ("speak",),
    "stole": ("steal",),
    "stolen": ("steal",),
    "struck": ("strike",),
    "swore": ("swear",),
    "sworn": ("swear",),
    "taught": ("teach",),
    "thought": ("think",),
    "took": ("take",),
    "undertaken": ("undertake",),
    "undertook": ("undertake",),
    "went": ("go",),
    "withdrew": ("withdraw",),
    "withdrawn": ("withdraw",),
    "woke": ("wake",),
    "woken": ("wake",),
    "wore": ("wear",),
    "worn": ("wear",),
    "wrote": ("write",),
    "written": ("write",),
}


class SubtitleFilterConfigurationError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class SubtitleFilterResult:
    text: str
    matched_terms: tuple[str, ...]
    source_unit_count: int
    kept_unit_count: int
    word_count: int
    truncated: bool


@dataclass(frozen=True, slots=True)
class _Phrase:
    term: str
    tokens: tuple[str, ...]
    level: CEFRLevel


@dataclass(frozen=True, slots=True)
class _Lexicon:
    singles: dict[str, CEFRLevel]
    phrases_by_first: dict[str, tuple[_Phrase, ...]]
    separable_phrases_by_first: dict[str, tuple[_Phrase, ...]]


@dataclass(frozen=True, slots=True)
class _Utterance:
    index: int
    text: str


@dataclass(frozen=True, slots=True)
class _Candidate:
    utterance: _Utterance
    hits: tuple[tuple[str, CEFRLevel, bool], ...]
    word_count: int


def _normalise_for_matching(value: str) -> str:
    value = value.translate(_APOSTROPHE_TRANSLATION)
    value = value.translate(_DASH_TRANSLATION).casefold()
    return " ".join(_WORD_RE.findall(value))


@lru_cache(maxsize=8_192)
def _lemma_candidates(token: str) -> tuple[str, ...]:
    forms = {token}
    forms.update(_IRREGULAR_LEMMAS.get(token, ()))

    if token.endswith("'s") and len(token) > 3:
        forms.add(token[:-2])
    if len(token) > 4 and token.endswith("ies"):
        forms.add(token[:-3] + "y")
        forms.add(token[:-1])
    if len(token) > 4 and token.endswith("ves"):
        forms.add(token[:-3] + "f")
        forms.add(token[:-3] + "fe")
    if len(token) > 3 and token.endswith("es"):
        forms.add(token[:-2])
        forms.add(token[:-1])
    elif len(token) > 3 and token.endswith("s") and not token.endswith("ss"):
        forms.add(token[:-1])

    if len(token) > 4 and token.endswith("ied"):
        forms.add(token[:-3] + "y")
    elif len(token) > 4 and token.endswith("ed"):
        stem = token[:-2]
        forms.add(stem)
        forms.add(stem + "e")
        if len(stem) > 2 and stem[-1] == stem[-2]:
            forms.add(stem[:-1])

    if len(token) > 5 and token.endswith("ing"):
        stem = token[:-3]
        forms.add(stem)
        forms.add(stem + "e")
        if len(stem) > 2 and stem[-1] == stem[-2]:
            forms.add(stem[:-1])

    if len(token) > 4 and token.endswith("ier"):
        forms.add(token[:-3] + "y")
    if len(token) > 5 and token.endswith("iest"):
        forms.add(token[:-4] + "y")
    if len(token) > 4 and token.endswith("er"):
        forms.add(token[:-2])
        forms.add(token[:-1])
    if len(token) > 5 and token.endswith("est"):
        forms.add(token[:-3])
        forms.add(token[:-2])

    return tuple(forms)


@lru_cache(maxsize=1)
def _load_lexicon() -> _Lexicon:
    resource = files("vocabulary").joinpath("data", "cefr_words.json")
    try:
        with resource.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)
    except (OSError, json.JSONDecodeError) as exc:
        raise SubtitleFilterConfigurationError(
            "The local CEFR vocabulary dataset could not be loaded."
        ) from exc

    entries = payload.get("entries") if isinstance(payload, dict) else None
    metadata = payload.get("_meta") if isinstance(payload, dict) else None
    if (
        not isinstance(entries, dict)
        or not isinstance(metadata, dict)
        or metadata.get("filter_version") != FILTER_VERSION
    ):
        raise SubtitleFilterConfigurationError(
            "The local CEFR vocabulary dataset is invalid."
        )

    singles: dict[str, CEFRLevel] = {}
    phrases_by_first: dict[str, list[_Phrase]] = {}
    separable_by_first: dict[str, list[_Phrase]] = {}
    for term, level in entries.items():
        if (
            not isinstance(term, str)
            or not term
            or level not in _LEVEL_WEIGHT
            or _normalise_for_matching(term) != term
        ):
            raise SubtitleFilterConfigurationError(
                "The local CEFR vocabulary dataset is invalid."
            )
        typed_level: CEFRLevel = level
        tokens = tuple(term.split())
        if len(tokens) == 1:
            singles[term] = typed_level
            continue
        phrase = _Phrase(term=term, tokens=tokens, level=typed_level)
        phrases_by_first.setdefault(tokens[0], []).append(phrase)
        if len(tokens) == 2 and tokens[1] in _PARTICLES:
            separable_by_first.setdefault(tokens[0], []).append(phrase)

    return _Lexicon(
        singles=singles,
        phrases_by_first={
            first: tuple(
                sorted(
                    values,
                    key=lambda item: (-len(item.tokens), item.term),
                )
            )
            for first, values in phrases_by_first.items()
        },
        separable_phrases_by_first={
            first: tuple(values) for first, values in separable_by_first.items()
        },
    )


def _split_utterances(text: str) -> list[_Utterance]:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    utterances: list[_Utterance] = []
    buffer: list[str] = []
    buffer_characters = 0

    def flush() -> None:
        nonlocal buffer, buffer_characters
        if buffer:
            utterances.append(
                _Utterance(index=len(utterances), text="\n".join(buffer))
            )
            buffer = []
            buffer_characters = 0

    for line in lines:
        if buffer and _SPEAKER_RE.match(line):
            flush()
        added_characters = len(line) + (1 if buffer else 0)
        if buffer and buffer_characters + added_characters > MAX_UTTERANCE_CHARACTERS:
            flush()
            added_characters = len(line)
        buffer.append(line)
        buffer_characters += added_characters
        if _TERMINAL_RE.search(line):
            flush()
    flush()
    return utterances


def _phrase_matches(
    tokens: tuple[str, ...],
    lexicon: _Lexicon,
) -> dict[str, tuple[CEFRLevel, bool]]:
    hits: dict[str, tuple[CEFRLevel, bool]] = {}
    token_forms = tuple(_lemma_candidates(token) for token in tokens)

    for index, forms in enumerate(token_forms):
        for form in forms:
            for phrase in lexicon.phrases_by_first.get(form, ()):
                end = index + len(phrase.tokens)
                if end > len(tokens):
                    continue
                if all(
                    expected in token_forms[position]
                    for position, expected in enumerate(
                        phrase.tokens,
                        start=index,
                    )
                ):
                    hits[phrase.term] = (phrase.level, True)

            for phrase in lexicon.separable_phrases_by_first.get(form, ()):
                particle = phrase.tokens[1]
                for particle_index in range(index + 1, min(index + 5, len(tokens))):
                    if particle in token_forms[particle_index]:
                        hits[phrase.term] = (phrase.level, True)
                        break

    return hits


def _matches_for_text(
    text: str,
    lexicon: _Lexicon,
) -> tuple[tuple[str, CEFRLevel, bool], ...]:
    normalised = _normalise_for_matching(text)
    if not normalised:
        return ()
    tokens = tuple(normalised.split())
    hits = _phrase_matches(tokens, lexicon)

    for token in tokens:
        for form in _lemma_candidates(token):
            level = lexicon.singles.get(form)
            if level is not None:
                hits[form] = (level, False)

    return tuple(
        (term, level, is_phrase)
        for term, (level, is_phrase) in sorted(hits.items())
    )


def _validate_budget(name: str, value: int) -> None:
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ValueError(f"{name} must be a positive integer")


def filter_subtitle(
    text: str,
    *,
    max_words: int = DEFAULT_MAX_WORDS,
    max_characters: int = DEFAULT_MAX_CHARACTERS,
) -> SubtitleFilterResult:
    """Select complete advanced-vocabulary utterances within a source budget."""
    if not isinstance(text, str):
        raise TypeError("text must be a string")
    _validate_budget("max_words", max_words)
    _validate_budget("max_characters", max_characters)

    utterances = _split_utterances(text)
    lexicon = _load_lexicon()
    candidates: list[_Candidate] = []
    seen_text: set[str] = set()
    for utterance in utterances:
        duplicate_key = _normalise_for_matching(utterance.text)
        if not duplicate_key or duplicate_key in seen_text:
            continue
        seen_text.add(duplicate_key)
        hits = _matches_for_text(utterance.text, lexicon)
        if not hits:
            continue
        candidates.append(
            _Candidate(
                utterance=utterance,
                hits=hits,
                word_count=len(_WORD_RE.findall(utterance.text)),
            )
        )

    selected: list[_Candidate] = []
    remaining = candidates.copy()
    matched_terms: set[str] = set()
    used_words = 0
    used_characters = 0
    while remaining:
        def priority(candidate: _Candidate) -> tuple[int, int, int, int]:
            novelty = sum(
                _LEVEL_WEIGHT[level]
                for term, level, _ in candidate.hits
                if term not in matched_terms
            )
            total = sum(
                _LEVEL_WEIGHT[level] + (2 if is_phrase else 0)
                for _, level, is_phrase in candidate.hits
            )
            return (
                novelty,
                total,
                -candidate.word_count,
                -candidate.utterance.index,
            )

        candidate = max(remaining, key=priority)
        remaining.remove(candidate)
        separator_characters = 1 if selected else 0
        if (
            used_words + candidate.word_count > max_words
            or used_characters + separator_characters + len(candidate.utterance.text)
            > max_characters
        ):
            continue
        selected.append(candidate)
        used_words += candidate.word_count
        used_characters += separator_characters + len(candidate.utterance.text)
        matched_terms.update(term for term, _, _ in candidate.hits)

    selected.sort(key=lambda candidate: candidate.utterance.index)
    filtered_text = "\n".join(
        candidate.utterance.text for candidate in selected
    )
    ordered_terms = tuple(
        sorted(
            matched_terms,
            key=lambda term: (
                -max(
                    _LEVEL_WEIGHT[level]
                    for candidate in selected
                    for matched_term, level, _ in candidate.hits
                    if matched_term == term
                ),
                term,
            ),
        )
    )
    return SubtitleFilterResult(
        text=filtered_text,
        matched_terms=ordered_terms,
        source_unit_count=len(utterances),
        kept_unit_count=len(selected),
        word_count=used_words,
        truncated=len(selected) < len(candidates),
    )


def filter_subtitle_text(
    text: str,
    *,
    max_words: int = DEFAULT_MAX_WORDS,
    max_characters: int = DEFAULT_MAX_CHARACTERS,
) -> str:
    return filter_subtitle(
        text,
        max_words=max_words,
        max_characters=max_characters,
    ).text


def filter_subtitle_document(
    document: SourceDocument,
    *,
    max_words: int | None = None,
    max_characters: int | None = None,
) -> SourceDocument:
    """Return a source document containing only whole advanced utterances."""
    if not isinstance(document, SourceDocument):
        raise TypeError("document must be a SourceDocument")
    filtered_text = filter_subtitle_text(
        document.text,
        max_words=DEFAULT_MAX_WORDS if max_words is None else max_words,
        max_characters=(
            DEFAULT_MAX_CHARACTERS
            if max_characters is None
            else max_characters
        ),
    )
    return SourceDocument(
        text=filtered_text,
        format=document.format,
        filename=document.filename,
        pre_filtered=True,
    )
