import re
from dataclasses import dataclass
from functools import lru_cache


_WORD_RE = re.compile(r"[^\W\d_]+(?:'[^\W\d_]+)?", re.UNICODE)
_APOSTROPHE_TRANSLATION = str.maketrans(
    {
        "\u2018": "'",
        "\u2019": "'",
        "\u02bc": "'",
        "\uff07": "'",
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
_CONTRACTIONS: dict[str, tuple[str, ...]] = {
    "can't": ("can", "not"),
    "shan't": ("shall", "not"),
    "won't": ("will", "not"),
}
_IRREGULAR_LEMMAS: dict[str, tuple[str, ...]] = {
    "arisen": ("arise",),
    "arose": ("arise",),
    "bought": ("buy",),
    "brought": ("bring",),
    "became": ("become",),
    "began": ("begin",),
    "begun": ("begin",),
    "better": ("good",),
    "broke": ("break",),
    "broken": ("break",),
    "caught": ("catch",),
    "dealt": ("deal",),
    "did": ("do",),
    "does": ("do",),
    "done": ("do",),
    "drove": ("drive",),
    "driven": ("drive",),
    "fell": ("fall",),
    "fallen": ("fall",),
    "felt": ("feel",),
    "fled": ("flee",),
    "found": ("find",),
    "gave": ("give",),
    "given": ("give",),
    "gone": ("go",),
    "got": ("get",),
    "gotten": ("get",),
    "grew": ("grow",),
    "grown": ("grow",),
    "held": ("hold",),
    "heard": ("hear",),
    "had": ("have",),
    "has": ("have",),
    "kept": ("keep",),
    "knew": ("know",),
    "known": ("know",),
    "led": ("lead",),
    "left": ("leave",),
    "made": ("make",),
    "meant": ("mean",),
    "met": ("meet",),
    "paid": ("pay",),
    "ran": ("run",),
    "read": ("read",),
    "risen": ("rise",),
    "rose": ("rise",),
    "said": ("say",),
    "saw": ("see",),
    "seen": ("see",),
    "sold": ("sell",),
    "sent": ("send",),
    "shaken": ("shake",),
    "shook": ("shake",),
    "spoke": ("speak",),
    "spoken": ("speak",),
    "taught": ("teach",),
    "thought": ("think",),
    "told": ("tell",),
    "took": ("take",),
    "taken": ("take",),
    "understood": ("understand",),
    "was": ("be",),
    "were": ("be",),
    "went": ("go",),
    "worse": ("bad",),
    "worn": ("wear",),
    "wore": ("wear",),
    "won": ("win",),
    "woke": ("wake",),
    "woken": ("wake",),
    "wrote": ("write",),
    "written": ("write",),
}
_IRREGULAR_LEMMAS.update(
    {
        "children": ("child",),
        "feet": ("foot",),
        "geese": ("goose",),
        "men": ("man",),
        "mice": ("mouse",),
        "people": ("person",),
        "teeth": ("tooth",),
        "women": ("woman",),
    }
)
_PARTICLES = frozenset(
    {
        "about",
        "across",
        "after",
        "around",
        "away",
        "back",
        "by",
        "down",
        "in",
        "into",
        "off",
        "on",
        "out",
        "over",
        "through",
        "together",
        "up",
    }
)


@dataclass(frozen=True, slots=True)
class TermMatch:
    """One source occurrence represented by the surface spans it matched."""

    spans: tuple[tuple[int, int], ...]


@dataclass(frozen=True, slots=True)
class _Token:
    value: str
    start: int
    end: int


def _expand_token(token: str) -> tuple[str, ...]:
    if token in _CONTRACTIONS:
        return _CONTRACTIONS[token]
    if token.endswith("n't") and len(token) > 3:
        return token[:-3], "not"
    if token.endswith("'re") and len(token) > 3:
        return token[:-3], "are"
    if token.endswith("'ve") and len(token) > 3:
        return token[:-3], "have"
    if token.endswith("'ll") and len(token) > 3:
        return token[:-3], "will"
    if token.endswith("'m") and len(token) > 2:
        return token[:-2], "am"
    return (token,)


def _tokens_with_spans(value: str) -> tuple[_Token, ...]:
    # Both translations are one-character substitutions, so match offsets still
    # refer to the original string.
    normalised = value.translate(_APOSTROPHE_TRANSLATION)
    normalised = normalised.translate(_DASH_TRANSLATION)
    expanded: list[_Token] = []
    for match in _WORD_RE.finditer(normalised):
        token = match.group(0).casefold()
        for value_part in _expand_token(token):
            expanded.append(_Token(value_part, match.start(), match.end()))
    return tuple(expanded)


def normalise_tokens(value: str) -> tuple[str, ...]:
    return tuple(token.value for token in _tokens_with_spans(value))


@lru_cache(maxsize=16_384)
def lemma_candidates(token: str) -> frozenset[str]:
    forms = {token}
    forms.update(_IRREGULAR_LEMMAS.get(token, ()))

    if token.endswith("'s") and len(token) > 3:
        forms.add(token[:-2])
    if len(token) > 4 and token.endswith("ies"):
        forms.add(token[:-3] + "y")
    elif len(token) > 4 and token.endswith("ves"):
        forms.add(token[:-3] + "f")
        forms.add(token[:-3] + "fe")
    elif len(token) > 4 and token.endswith(("ches", "shes", "sses", "xes", "zes")):
        forms.add(token[:-2])
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
    elif len(token) > 4 and token.endswith("er"):
        stem = token[:-2]
        forms.add(stem)
        forms.add(stem + "e")
        if len(stem) > 2 and stem[-1] == stem[-2]:
            forms.add(stem[:-1])

    if len(token) > 5 and token.endswith("iest"):
        forms.add(token[:-4] + "y")
    elif len(token) > 5 and token.endswith("est"):
        stem = token[:-3]
        forms.add(stem)
        forms.add(stem + "e")
        if len(stem) > 2 and stem[-1] == stem[-2]:
            forms.add(stem[:-1])

    if len(token) > 5 and token.endswith("ily"):
        forms.add(token[:-3] + "y")
    elif len(token) > 5 and token.endswith("ly"):
        stem = token[:-2]
        forms.add(stem)
        if stem.endswith("ical"):
            forms.add(stem[:-2])

    return frozenset(forms)


def _tokens_match(left: str, right: str) -> bool:
    return bool(lemma_candidates(left) & lemma_candidates(right))


def find_term_matches(source_text: str, term: str) -> tuple[TermMatch, ...]:
    """Return morphological term matches with their original surface spans."""
    target = term.strip()
    if not target:
        return ()

    exact_pattern = re.compile(rf"(?<!\w){re.escape(target)}(?!\w)", re.IGNORECASE)
    exact_spans = tuple(match.span() for match in exact_pattern.finditer(source_text))
    matches: set[tuple[tuple[int, int], ...]] = {
        (span,) for span in exact_spans
    }

    def overlaps_exact(spans: tuple[tuple[int, int], ...]) -> bool:
        return any(
            span_start < exact_end and exact_start < span_end
            for span_start, span_end in spans
            for exact_start, exact_end in exact_spans
        )

    source_tokens = _tokens_with_spans(source_text)
    term_tokens = normalise_tokens(target)
    if not source_tokens or not term_tokens:
        return tuple(TermMatch(spans) for spans in sorted(matches))

    term_length = len(term_tokens)
    for start in range(len(source_tokens) - term_length + 1):
        if all(
            _tokens_match(source_tokens[start + offset].value, expected)
            for offset, expected in enumerate(term_tokens)
        ):
            first = source_tokens[start]
            last = source_tokens[start + term_length - 1]
            spans = ((first.start, last.end),)
            if not overlaps_exact(spans):
                matches.add(spans)

    if len(term_tokens) == 2 and term_tokens[1] in _PARTICLES:
        verb, particle = term_tokens
        for start, source_token in enumerate(source_tokens):
            if not _tokens_match(source_token.value, verb):
                continue
            for offset, following in enumerate(
                source_tokens[start + 1 : start + 5],
                start=1,
            ):
                if not _tokens_match(following.value, particle):
                    continue
                # The contiguous pass already recorded the adjacent form as one
                # blankable span. Only add genuinely separated phrasal verbs here.
                if offset == 1:
                    continue
                spans = (
                    (source_token.start, source_token.end),
                    (following.start, following.end),
                )
                if not overlaps_exact(spans):
                    matches.add(spans)

    return tuple(TermMatch(spans) for spans in sorted(matches))


def source_contains_term(source_text: str, term: str) -> bool:
    return bool(find_term_matches(source_text, term))
