from enum import StrEnum

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictStr,
    field_validator,
    model_validator,
)

from .constants import MAX_GENERATION_CANDIDATES, MAX_GENERATION_ITEMS
from .text import BlankSentenceError, validate_blank_sentence


def _clean_bounded_text(value: str, *, max_length: int) -> str:
    cleaned = value.strip()
    if not cleaned:
        raise ValueError("Text values must not be empty.")
    if len(cleaned) > max_length:
        raise ValueError(f"Text values must not exceed {max_length} characters.")
    return cleaned


class VocabularyType(StrEnum):
    PHRASAL_VERB = "phrasal_verb"
    IDIOM = "idiom"
    COLLOCATION = "collocation"
    NOUN = "noun"
    ADJECTIVE = "adjective"
    VERB = "verb"
    ADVERB = "adverb"
    OTHER = "other"


class CefrLevel(StrEnum):
    B2 = "B2"
    C1 = "C1"
    C2 = "C2"


class VocabularyItemCandidate(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        populate_by_name=True,
        revalidate_instances="always",
    )

    word_or_phrase: StrictStr
    type: VocabularyType
    cefr_level: CefrLevel = Field(alias="CEFR_level")
    definition_en: StrictStr
    example_sentence: StrictStr

    @field_validator("word_or_phrase")
    @classmethod
    def clean_short_text(cls, value: str) -> str:
        return _clean_bounded_text(value, max_length=255)

    @field_validator("example_sentence")
    @classmethod
    def clean_sentence_text(cls, value: str) -> str:
        return _clean_bounded_text(value, max_length=1000)

    @field_validator("definition_en")
    @classmethod
    def clean_definition(cls, value: str) -> str:
        return _clean_bounded_text(value, max_length=1500)


class VocabularyItemResponse(VocabularyItemCandidate):
    blank_sentence: StrictStr

    @model_validator(mode="after")
    def blank_is_derived_from_example(self) -> "VocabularyItemResponse":
        try:
            validate_blank_sentence(
                self.word_or_phrase,
                self.example_sentence,
                self.blank_sentence,
            )
        except BlankSentenceError as exc:
            raise ValueError(str(exc)) from exc
        return self


class VocabularyExtractionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")

    movie_title: StrictStr
    items: list[VocabularyItemResponse] = Field(
        min_length=1,
        max_length=MAX_GENERATION_ITEMS,
    )

    @field_validator("movie_title")
    @classmethod
    def clean_movie_title(cls, value: str) -> str:
        return _clean_bounded_text(value, max_length=255)


class VocabularyExtractionCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid", revalidate_instances="always")

    movie_title: StrictStr
    items: list[VocabularyItemCandidate] = Field(
        min_length=1,
        max_length=MAX_GENERATION_CANDIDATES,
    )

    @field_validator("movie_title")
    @classmethod
    def clean_movie_title(cls, value: str) -> str:
        return _clean_bounded_text(value, max_length=255)
