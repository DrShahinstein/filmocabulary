import re


BLANK_TOKEN = "___"


class BlankSentenceError(ValueError):
    """Raised when a quiz blank cannot be derived unambiguously."""


def derive_blank_sentence(word_or_phrase: str, example_sentence: str) -> str:
    """Replace one exact, case-insensitive target occurrence with the blank token."""
    target = word_or_phrase.strip()
    sentence = example_sentence.strip()
    if not target or not sentence:
        raise BlankSentenceError("The target and example sentence are required.")
    if BLANK_TOKEN in sentence:
        raise BlankSentenceError("The example sentence must not already contain a blank.")

    pattern = re.compile(rf"(?<!\w){re.escape(target)}(?!\w)", re.IGNORECASE)
    matches = list(pattern.finditer(sentence))
    if len(matches) != 1:
        raise BlankSentenceError(
            "The example sentence must contain the target exactly once."
        )
    return pattern.sub(BLANK_TOKEN, sentence, count=1)


def validate_blank_sentence(
    word_or_phrase: str,
    example_sentence: str,
    blank_sentence: str,
) -> str:
    derived = derive_blank_sentence(word_or_phrase, example_sentence)
    if blank_sentence.strip() != derived:
        raise BlankSentenceError(
            "The blank sentence must exactly match the sentence with its target replaced."
        )
    return derived

