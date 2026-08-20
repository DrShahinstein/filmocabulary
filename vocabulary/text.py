from enum import StrEnum

from .matching import find_term_matches


BLANK_TOKEN = "___"


class BlankSentenceErrorReason(StrEnum):
    MISSING_TARGET = "missing_target"
    AMBIGUOUS_TARGET = "ambiguous_target"
    PREEXISTING_BLANK = "preexisting_blank"
    MISSING_TEXT = "missing_text"
    MISMATCHED_BLANK = "mismatched_blank"


class BlankSentenceError(ValueError):
    """Raised when a quiz blank cannot be derived unambiguously."""

    def __init__(self, message: str, *, reason: BlankSentenceErrorReason) -> None:
        super().__init__(message)
        self.reason = reason


def derive_blank_sentence(word_or_phrase: str, example_sentence: str) -> str:
    """Replace one morphological target occurrence with one or more blanks."""
    target = word_or_phrase.strip()
    sentence = example_sentence.strip()
    if not target or not sentence:
        raise BlankSentenceError(
            "The target and example sentence are required.",
            reason=BlankSentenceErrorReason.MISSING_TEXT,
        )
    if BLANK_TOKEN in sentence:
        raise BlankSentenceError(
            "The example sentence must not already contain a blank.",
            reason=BlankSentenceErrorReason.PREEXISTING_BLANK,
        )

    matches = find_term_matches(sentence, target)
    if not matches:
        raise BlankSentenceError(
            "The example sentence must contain the target or an inflected form.",
            reason=BlankSentenceErrorReason.MISSING_TARGET,
        )
    if len(matches) != 1:
        raise BlankSentenceError(
            "The example sentence must contain one unambiguous target occurrence.",
            reason=BlankSentenceErrorReason.AMBIGUOUS_TARGET,
        )

    blank_sentence = sentence
    for start, end in reversed(matches[0].spans):
        blank_sentence = blank_sentence[:start] + BLANK_TOKEN + blank_sentence[end:]
    return blank_sentence


def validate_blank_sentence(
    word_or_phrase: str,
    example_sentence: str,
    blank_sentence: str,
) -> str:
    derived = derive_blank_sentence(word_or_phrase, example_sentence)
    if blank_sentence.strip() != derived:
        raise BlankSentenceError(
            "The blank sentence must exactly match the sentence with its target replaced.",
            reason=BlankSentenceErrorReason.MISMATCHED_BLANK,
        )
    return derived
