import json

from django.test import SimpleTestCase
from pydantic import ValidationError

from vocabulary.schemas import VocabularyExtractionCandidate, VocabularyExtractionResponse
from vocabulary.services import SYSTEM_PROMPT

from .factories import vocabulary_payload


class VocabularyResponseSchemaTests(SimpleTestCase):
    def test_validates_strict_payload_and_cefr_alias(self):
        response = VocabularyExtractionResponse.model_validate(
            vocabulary_payload(include_blank=True)
        )

        self.assertEqual(response.movie_title, "Zodiac")
        self.assertEqual(response.items[0].cefr_level.value, "C1")
        self.assertEqual(
            response.items[0].blank_sentence,
            "The reporter chose to ___ every small detail.",
        )

    def test_accepts_b1_as_a_valid_backfill_level(self):
        payload = vocabulary_payload(include_blank=True)
        payload["items"][0]["CEFR_level"] = "B1"

        response = VocabularyExtractionResponse.model_validate(payload)

        self.assertEqual(response.items[0].cefr_level.value, "B1")

    def test_accepts_one_hundred_vocabulary_items(self):
        response = VocabularyExtractionResponse.model_validate(
            vocabulary_payload(item_count=100, include_blank=True)
        )

        self.assertEqual(len(response.items), 100)
        schema = VocabularyExtractionResponse.model_json_schema()
        self.assertEqual(schema["properties"]["items"]["maxItems"], 100)

    def test_rejects_more_than_one_hundred_vocabulary_items(self):
        with self.assertRaises(ValidationError):
            VocabularyExtractionResponse.model_validate(
                vocabulary_payload(item_count=101, include_blank=True)
            )

    def test_provider_schema_allows_fifteen_surplus_candidates(self):
        response = VocabularyExtractionCandidate.model_validate(
            vocabulary_payload(item_count=115)
        )

        self.assertEqual(len(response.items), 115)
        schema = VocabularyExtractionCandidate.model_json_schema()
        self.assertEqual(schema["properties"]["items"]["maxItems"], 115)

    def test_provider_schema_rejects_more_than_surplus_limit(self):
        with self.assertRaises(ValidationError):
            VocabularyExtractionCandidate.model_validate(
                vocabulary_payload(item_count=116)
            )

    def test_rejects_extra_fields(self):
        payload = vocabulary_payload(include_blank=True)
        payload["items"][0]["spoiler"] = "not allowed"

        with self.assertRaises(ValidationError):
            VocabularyExtractionResponse.model_validate(payload)

    def test_rejects_deprecated_translation_field(self):
        payload = vocabulary_payload(include_blank=True)
        payload["items"][0]["translation_tr"] = "dikkatle incelemek"

        with self.assertRaises(ValidationError):
            VocabularyExtractionResponse.model_validate(payload)

    def test_rejects_non_string_text_instead_of_coercing_it(self):
        payload = vocabulary_payload(include_blank=True)
        payload["items"][0]["definition_en"] = 123

        with self.assertRaises(ValidationError):
            VocabularyExtractionResponse.model_validate(payload)

    def test_rejects_a_blank_not_derived_from_the_example(self):
        payload = vocabulary_payload(include_blank=True)
        payload["items"][0]["blank_sentence"] = "A different ___ sentence."

        with self.assertRaises(ValidationError):
            VocabularyExtractionResponse.model_validate(payload)

    def test_rejects_an_ambiguous_target_occurrence(self):
        payload = vocabulary_payload(include_blank=True)
        payload["items"][0]["example_sentence"] = (
            "They scrutinize a clue, then scrutinize it again."
        )
        payload["items"][0]["blank_sentence"] = (
            "They ___ a clue, then scrutinize it again."
        )

        with self.assertRaises(ValidationError):
            VocabularyExtractionResponse.model_validate(payload)

    def test_prompt_contains_required_guardrails(self):
        self.assertIn(
            "Primary: Extract all genuine, high-value B2, C1, and C2 terms present in the source.",
            SYSTEM_PROMPT,
        )
        self.assertIn("expressive, figurative, cinematic, literary, or nuanced", SYSTEM_PROMPT)
        self.assertIn("If and only if the source genuinely lacks enough qualifying B2-C2 terms", SYSTEM_PROMPT)
        self.assertIn("Quality outranks count.", SYSTEM_PROMPT)
        self.assertIn("plain, functional, generic, predictable, or trivial", SYSTEM_PROMPT)
        self.assertIn('"brother-in-law" and "aunt"', SYSTEM_PROMPT)
        self.assertIn('"divorced" and "single"', SYSTEM_PROMPT)
        self.assertIn('"beloved" and "nice"', SYSTEM_PROMPT)
        self.assertIn(
            "assign CEFR solely from their lexical rarity, idiomatic complexity, and recognized pedagogical difficulty in standard English",
            SYSTEM_PROMPT,
        )
        self.assertIn(
            '"mental projection" is not advanced merely because its concept sounds complex',
            SYSTEM_PROMPT,
        )
        self.assertIn(
            "independently of the film's lore, plot, world-building, or narrative depth",
            SYSTEM_PROMPT,
        )
        self.assertIn("Verbatim source grounding is non-negotiable", SYSTEM_PROMPT)
        self.assertIn("NEVER invent, hallucinate", SYSTEM_PROMPT)
        self.assertIn(
            "Do not reveal plot twists, endings, culprits, betrayals, key character deaths, or resolutions.",
            SYSTEM_PROMPT,
        )
        self.assertIn("literal base/uninflected form", SYSTEM_PROMPT)
        self.assertIn("outside the required JSON schema", SYSTEM_PROMPT)

    def test_provider_schema_uses_aliases_and_forbids_extra_properties(self):
        response_schema = VocabularyExtractionResponse.model_json_schema(by_alias=True)
        response_item_schema = response_schema["$defs"]["VocabularyItemResponse"]
        provider_schema = VocabularyExtractionCandidate.model_json_schema(by_alias=True)
        provider_item_schema = provider_schema["$defs"]["VocabularyItemCandidate"]
        encoded_schema = json.dumps(provider_schema)

        self.assertFalse(response_schema["additionalProperties"])
        self.assertFalse(response_item_schema["additionalProperties"])
        self.assertFalse(provider_schema["additionalProperties"])
        self.assertFalse(provider_item_schema["additionalProperties"])
        self.assertIn("CEFR_level", provider_item_schema["properties"])
        self.assertNotIn("translation_tr", provider_item_schema["properties"])
        self.assertNotIn("blank_sentence", provider_item_schema["properties"])
        self.assertIn("blank_sentence", response_item_schema["properties"])
        self.assertNotIn("maxLength", encoded_schema)
        self.assertNotIn("minLength", encoded_schema)
