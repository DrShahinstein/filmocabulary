import json
from types import SimpleNamespace
from unittest.mock import Mock, patch

import httpx
from django.contrib.auth import get_user_model
from django.db import IntegrityError
from django.test import TestCase, override_settings
from openai import OpenAI

from movies.models import Movie
from vocabulary.ingestion import SourceDocument
from vocabulary.models import VocabularyItem
from vocabulary.schemas import (
    VocabularyExtractionCandidate,
)
from vocabulary.services import (
    VocabularyYieldReason,
    VocabularyConfigurationError,
    VocabularyPersistenceError,
    VocabularyProviderError,
    VocabularyResponseError,
    _candidate_limit,
    benchmark_vocabulary_prompt,
    generate_and_save_vocabulary,
)

from .factories import vocabulary_payload


@override_settings(
    LLM_MODEL="test-structured-model",
    LLM_REASONING_EFFORT="none",
    LLM_MAX_TOKENS_PARAMETER="max_tokens",
    LLM_EDITORIAL_REVIEW=False,
)
class VocabularyGenerationServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="learner", password="test-pass-42"
        )

    def llm_client_with_payload(self, payload, *, finish_reason="stop"):
        if hasattr(payload, "model_dump"):
            payload = payload.model_dump(mode="json", by_alias=True)
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(
            usage=SimpleNamespace(
                prompt_tokens=1_500,
                completion_tokens=600,
                total_tokens=2_100,
                prompt_tokens_details=SimpleNamespace(cached_tokens=100),
            ),
            choices=[
                SimpleNamespace(
                    finish_reason=finish_reason,
                    message=SimpleNamespace(content=json.dumps(payload)),
                )
            ]
        )
        return client

    def test_candidate_limit_uses_proportional_floor_and_ceiling(self):
        self.assertEqual(
            {
                item_count: _candidate_limit(item_count)
                for item_count in (1, 5, 30, 80, 100)
            },
            {1: 4, 5: 8, 30: 36, 80: 95, 100: 115},
        )

    def test_creates_movie_and_validated_items_atomically(self):
        parsed = VocabularyExtractionCandidate.model_validate(vocabulary_payload())
        client = self.llm_client_with_payload(parsed)

        result = generate_and_save_vocabulary(
            user=self.user,
            title="Zodiac",
            release_year=2007,
            item_count=5,
            client=client,
        )

        self.assertTrue(result.movie_created)
        self.assertEqual(result.created_count, 1)
        self.assertEqual(result.skipped_count, 0)
        self.assertEqual(Movie.objects.get().user, self.user)
        self.assertEqual(VocabularyItem.objects.get().blank_sentence.count("___"), 1)
        request_kwargs = client.chat.completions.create.call_args.kwargs
        self.assertEqual(request_kwargs["model"], "test-structured-model")
        self.assertEqual(request_kwargs["max_tokens"], 1_792)
        self.assertEqual(request_kwargs["reasoning_effort"], "none")
        self.assertEqual(request_kwargs["response_format"]["type"], "json_schema")

    def test_accepts_and_persists_one_hundred_items(self):
        parsed = VocabularyExtractionCandidate.model_validate(
            vocabulary_payload(item_count=100)
        )
        client = self.llm_client_with_payload(parsed)

        result = generate_and_save_vocabulary(
            user=self.user,
            title="Zodiac",
            release_year=2007,
            item_count=100,
            client=client,
        )

        self.assertEqual(result.created_count, 100)
        self.assertEqual(VocabularyItem.objects.count(), 100)
        prompt = client.chat.completions.create.call_args.kwargs["messages"][1][
            "content"
        ]
        self.assertIn('"candidate_limit": 115', prompt)

    def test_accepts_and_persists_b1_backfill_item(self):
        payload = vocabulary_payload(word="abandon")
        payload["items"][0]["CEFR_level"] = "B1"
        client = self.llm_client_with_payload(payload)

        result = generate_and_save_vocabulary(
            user=self.user,
            title="Zodiac",
            release_year=2007,
            item_count=1,
            client=client,
        )

        self.assertEqual(result.created_count, 1)
        self.assertEqual(VocabularyItem.objects.get().cefr_level, "B1")

    def test_trims_provider_overrun_and_keeps_requested_count(self):
        client = self.llm_client_with_payload(vocabulary_payload(item_count=18))

        result = generate_and_save_vocabulary(
            user=self.user,
            title="Zodiac",
            release_year=2007,
            item_count=2,
            client=client,
        )

        self.assertEqual(result.created_count, 2)
        self.assertEqual(VocabularyItem.objects.count(), 2)
        prompt = client.chat.completions.create.call_args.kwargs["messages"][1][
            "content"
        ]
        self.assertIn('"candidate_limit": 5', prompt)

    def test_rejects_item_count_above_one_hundred(self):
        with self.assertRaisesRegex(
            ValueError, "item_count must be between 1 and 100"
        ):
            generate_and_save_vocabulary(
                user=self.user,
                title="Zodiac",
                release_year=2007,
                item_count=101,
                client=Mock(),
            )

        self.assertFalse(Movie.objects.exists())

    def test_generic_client_uses_structured_output_contract(self):
        client = self.llm_client_with_payload(vocabulary_payload(item_count=8))

        result = generate_and_save_vocabulary(
            user=self.user,
            title="Zodiac",
            release_year=2007,
            item_count=5,
            client=client,
        )

        self.assertTrue(result.movie_created)
        self.assertEqual(result.created_count, 5)
        request_kwargs = client.chat.completions.create.call_args.kwargs
        self.assertEqual(request_kwargs["model"], "test-structured-model")
        self.assertEqual(request_kwargs["max_tokens"], 1_792)
        self.assertEqual(request_kwargs["reasoning_effort"], "none")
        self.assertNotIn("extra_body", request_kwargs)
        self.assertIn(
            "cross-context usefulness",
            request_kwargs["messages"][0]["content"],
        )
        self.assertIn(
            "same lexeme as the source occurrence",
            request_kwargs["messages"][0]["content"],
        )
        self.assertIn(
            "only exceptional high-utility B1 items",
            request_kwargs["messages"][1]["content"],
        )
        self.assertIn(
            "quality determines the final count",
            request_kwargs["messages"][1]["content"],
        )
        self.assertIn(
            '"candidate_limit": 8',
            request_kwargs["messages"][1]["content"],
        )
        self.assertNotIn(
            "Generate exactly",
            request_kwargs["messages"][1]["content"],
        )
        self.assertNotIn(
            "Do not stop early",
            request_kwargs["messages"][1]["content"],
        )
        self.assertIn(
            '"movie_reference": "Zodiac (2007)"',
            request_kwargs["messages"][1]["content"],
        )
        response_format = request_kwargs["response_format"]
        self.assertEqual(response_format["type"], "json_schema")
        item_schema = response_format["json_schema"]["schema"]["$defs"][
            "VocabularyItemCandidate"
        ]
        collection_schema = response_format["json_schema"]["schema"]["properties"][
            "items"
        ]
        self.assertEqual(collection_schema["minItems"], 1)
        self.assertEqual(collection_schema["maxItems"], 8)
        self.assertIn("CEFR_level", item_schema["properties"])
        self.assertNotIn("blank_sentence", item_schema["properties"])
        client.close.assert_not_called()

    def test_generic_client_scales_output_limit_for_thirty_items(self):
        client = self.llm_client_with_payload(vocabulary_payload(item_count=36))

        generate_and_save_vocabulary(
            user=self.user,
            title="Zodiac",
            release_year=2007,
            item_count=30,
            client=client,
        )

        request_kwargs = client.chat.completions.create.call_args.kwargs
        self.assertEqual(request_kwargs["max_tokens"], 6_272)

    @override_settings(LLM_EDITORIAL_REVIEW=True)
    def test_editorial_review_filters_inflected_verb_headwords(self):
        initial_payload = vocabulary_payload(item_count=2)
        reviewed_payload = vocabulary_payload(item_count=2)
        reviewed_payload["items"][1].update(
            {
                "word_or_phrase": "scrambling",
                "definition_en": "Moving quickly in a disorganized way.",
                "example_sentence": "The staff were scrambling to finish the work.",
            }
        )
        client = self.llm_client_with_payload(initial_payload)
        initial_response = client.chat.completions.create.return_value
        reviewed_response = SimpleNamespace(
            usage=initial_response.usage,
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content=json.dumps(reviewed_payload)),
                )
            ],
        )
        client.chat.completions.create.side_effect = [
            initial_response,
            reviewed_response,
        ]

        result = generate_and_save_vocabulary(
            user=self.user,
            title="Zodiac",
            release_year=2007,
            item_count=2,
            client=client,
        )

        self.assertEqual(client.chat.completions.create.call_count, 2)
        review_request = client.chat.completions.create.call_args_list[1].kwargs
        self.assertIn(
            "final editor of an ESL vocabulary deck",
            review_request["messages"][0]["content"],
        )
        self.assertEqual(result.created_count, 1)
        self.assertFalse(
            VocabularyItem.objects.filter(word_or_phrase="scrambling").exists()
        )

    @override_settings(LLM_EDITORIAL_REVIEW=True)
    def test_benchmark_reports_editorial_filtering_separately(self):
        initial_payload = vocabulary_payload(item_count=3)
        reviewed_payload = vocabulary_payload(item_count=2)
        client = self.llm_client_with_payload(initial_payload)
        initial_response = client.chat.completions.create.return_value
        reviewed_response = SimpleNamespace(
            usage=initial_response.usage,
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content=json.dumps(reviewed_payload)),
                )
            ],
        )
        client.chat.completions.create.side_effect = [
            initial_response,
            reviewed_response,
        ]

        result = benchmark_vocabulary_prompt(
            movie_title="Zodiac",
            candidate_limit=3,
            client=client,
        )

        self.assertEqual(result.extraction_returned_count, 3)
        self.assertEqual(result.provider_returned_count, 2)
        self.assertEqual(result.editorial_filtered_count, 1)
        self.assertEqual(result.accepted_count, 2)
        self.assertEqual(result.rejected_count, 1)

    def test_generic_client_logs_usage_without_response_content(self):
        client = self.llm_client_with_payload(vocabulary_payload(item_count=8))

        with self.assertLogs("vocabulary.usage", level="INFO") as logs:
            generate_and_save_vocabulary(
                user=self.user,
                title="Zodiac",
                release_year=2007,
                item_count=5,
                client=client,
            )

        usage_log = "\n".join(logs.output)
        self.assertIn("prompt_tokens=1500", usage_log)
        self.assertIn("completion_tokens=600", usage_log)
        self.assertIn("total_tokens=2100", usage_log)
        self.assertIn("cached_prompt_tokens=100", usage_log)
        self.assertIn("candidate_limit=8", usage_log)
        self.assertIn("reasoning_effort=none", usage_log)
        self.assertNotIn("scrutinize", usage_log)

    @override_settings(
        LLM_MODEL="vendor/model-contract-test",
    )
    def test_generic_openai_compatibility_transport_contract(self):
        captured_request = {}

        def handle_request(request):
            captured_request["url"] = str(request.url)
            captured_request["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "id": "test-completion",
                    "object": "chat.completion",
                    "created": 0,
                    "model": "vendor/model-contract-test",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(vocabulary_payload(item_count=8)),
                            },
                            "finish_reason": "stop",
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 1500,
                        "completion_tokens": 600,
                        "total_tokens": 2100,
                        "prompt_tokens_details": {"cached_tokens": 100},
                    },
                },
            )

        http_client = httpx.Client(transport=httpx.MockTransport(handle_request))
        provider_client = OpenAI(
            api_key="generic-contract-test-key",
            base_url="https://llm.example/v1",
            http_client=http_client,
        )
        self.addCleanup(provider_client.close)

        result = generate_and_save_vocabulary(
            user=self.user,
            title="Zodiac",
            release_year=2007,
            item_count=5,
            client=provider_client,
        )

        self.assertEqual(result.created_count, 5)
        self.assertEqual(
            captured_request["url"],
            "https://llm.example/v1/chat/completions",
        )
        body = captured_request["body"]
        self.assertEqual(body["max_tokens"], 1_792)
        self.assertEqual(body["reasoning_effort"], "none")
        self.assertNotIn("context_length_exceeded_behavior", body)
        self.assertEqual(body["response_format"]["type"], "json_schema")
        items_schema = body["response_format"]["json_schema"]["schema"][
            "properties"
        ]["items"]
        self.assertEqual(items_schema["minItems"], 1)
        self.assertEqual(items_schema["maxItems"], 8)
        self.assertIn(
            "CEFR_level",
            body["response_format"]["json_schema"]["schema"]["$defs"][
                "VocabularyItemCandidate"
            ]["properties"],
        )

    def test_reuses_owned_movie_and_skips_existing_term(self):
        parsed = VocabularyExtractionCandidate.model_validate(vocabulary_payload())
        client = self.llm_client_with_payload(parsed)
        first = generate_and_save_vocabulary(
            user=self.user, title="Zodiac", release_year=2007, client=client
        )

        second = generate_and_save_vocabulary(
            user=self.user, title="zodiac", release_year=2007, client=client
        )

        self.assertFalse(second.movie_created)
        self.assertEqual(second.movie, first.movie)
        self.assertEqual(second.created_count, 0)
        self.assertEqual(second.skipped_count, 1)
        self.assertEqual(Movie.objects.count(), 1)

    def test_rejects_wrong_movie_without_writing_to_database(self):
        payload = vocabulary_payload(movie_title="Arrival")
        client = self.llm_client_with_payload(payload)

        with self.assertRaises(VocabularyResponseError):
            generate_and_save_vocabulary(
                user=self.user, title="Zodiac", release_year=2007, client=client
            )

        self.assertFalse(Movie.objects.exists())
        self.assertFalse(VocabularyItem.objects.exists())

    def test_source_is_sent_as_untrusted_evidence_and_grounded_items_are_saved(self):
        parsed = VocabularyExtractionCandidate.model_validate(vocabulary_payload())
        client = self.llm_client_with_payload(parsed)
        source = SourceDocument(
            text="The reporter decided to scrutinize every detail.",
            format="script",
            filename="zodiac.txt",
        )

        result = generate_and_save_vocabulary(
            user=self.user,
            title="Zodiac",
            release_year=2007,
            item_count=5,
            client=client,
            source=source,
        )

        self.assertEqual(result.created_count, 1)
        user_prompt = client.chat.completions.create.call_args.kwargs["messages"][1][
            "content"
        ]
        self.assertIn("untrusted reference data", user_prompt)
        self.assertIn("SOURCE_TEXT_START", user_prompt)
        self.assertIn(source.text, user_prompt)

    def test_source_grounding_accepts_inflected_and_punctuated_forms(self):
        payload = vocabulary_payload(item_count=3)
        payload["items"][0]["word_or_phrase"] = "scrutinize"
        payload["items"][0]["example_sentence"] = (
            "The reporter chose to scrutinize every detail."
        )
        payload["items"][1]["word_or_phrase"] = "utensil"
        payload["items"][1]["example_sentence"] = "Each utensil was carefully packed."
        payload["items"][2]["word_or_phrase"] = "cutting edge"
        payload["items"][2]["example_sentence"] = (
            "The cutting edge method changed their approach."
        )
        source = SourceDocument(
            text=(
                "The reporter scrutinized every detail. "
                "They packed the utensils beside a cutting-edge device."
            ),
            format="script",
        )
        client = self.llm_client_with_payload(payload)

        result = generate_and_save_vocabulary(
            user=self.user,
            title="Zodiac",
            release_year=2007,
            item_count=3,
            client=client,
            source=source,
        )

        self.assertEqual(result.created_count, 3)

    def test_rejects_model_supplied_blank_not_present_in_wire_schema(self):
        payload = vocabulary_payload()
        payload["items"][0]["blank_sentence"] = "The model supplied the wrong ___."
        client = self.llm_client_with_payload(payload)

        with self.assertRaises(VocabularyResponseError):
            generate_and_save_vocabulary(
                user=self.user,
                title="Zodiac",
                release_year=2007,
                item_count=5,
                client=client,
            )

        self.assertFalse(VocabularyItem.objects.exists())

    def test_salvages_valid_siblings_and_classifies_malformed_candidate(self):
        payload = vocabulary_payload(item_count=2)
        payload["items"][1]["type"] = "invented_type"
        client = self.llm_client_with_payload(payload)

        result = generate_and_save_vocabulary(
            user=self.user,
            title="Zodiac",
            release_year=2007,
            item_count=2,
            client=client,
        )

        self.assertEqual(result.created_count, 1)
        self.assertEqual(result.provider_returned_count, 2)
        self.assertEqual(result.candidate_rejections.malformed, 1)
        self.assertEqual(result.schema_rejections.invalid_type, 1)
        self.assertEqual(
            result.yield_reasons,
            (VocabularyYieldReason.INVALID_SCHEMA,),
        )
        self.assertEqual(VocabularyItem.objects.count(), 1)
        client.chat.completions.create.assert_called_once()

    def test_inflected_example_is_cloze_eligible_without_a_refill(self):
        payload = vocabulary_payload(item_count=5)
        payload["items"][0]["example_sentence"] = (
            "The reporter scrutinized every small detail."
        )
        client = self.llm_client_with_payload(payload)

        result = generate_and_save_vocabulary(
            user=self.user,
            title="Zodiac",
            release_year=2007,
            item_count=2,
            client=client,
        )

        self.assertEqual(result.created_count, 2)
        inflected = VocabularyItem.objects.get(word_or_phrase="scrutinize")
        self.assertEqual(
            inflected.blank_sentence,
            "The reporter ___ every small detail.",
        )
        client.chat.completions.create.assert_called_once()

    def test_single_shot_saves_cloze_ineligible_candidates_without_refill(self):
        payload = vocabulary_payload(item_count=51)
        payload["items"][-1]["example_sentence"] = (
            "The reporter reviewed every small detail."
        )
        client = self.llm_client_with_payload(payload)

        with self.assertLogs("vocabulary.usage", level="INFO") as logs:
            result = generate_and_save_vocabulary(
                user=self.user,
                title="Zodiac",
                release_year=2007,
                item_count=80,
                client=client,
            )

        self.assertEqual(result.created_count, 51)
        self.assertEqual(result.requested_count, 80)
        self.assertEqual(result.provider_returned_count, 51)
        self.assertEqual(result.validated_candidate_count, 51)
        self.assertEqual(result.candidate_rejections.total, 0)
        self.assertEqual(result.cloze_ineligibility.missing_target, 1)
        self.assertEqual(
            result.yield_reasons,
            (VocabularyYieldReason.PROVIDER_SHORTFALL,),
        )
        self.assertEqual(VocabularyItem.objects.count(), 51)
        self.assertIsNone(
            VocabularyItem.objects.get(word_or_phrase="scrutinize-51").blank_sentence
        )
        client.chat.completions.create.assert_called_once()
        yield_log = "\n".join(logs.output)
        self.assertIn("initial_returned=51", yield_log)
        self.assertIn("candidate_limit=95", yield_log)
        self.assertIn("initial_accepted=51", yield_log)
        self.assertIn("initial_rejected=0", yield_log)
        self.assertIn("final_accepted=51", yield_log)
        self.assertNotIn("refill", yield_log)
        self.assertIn(
            "cloze_reasons=missing:1,ambiguous:0",
            yield_log,
        )

    def test_underfilled_response_remains_single_shot(self):
        payload = vocabulary_payload(item_count=3)
        payload["items"][-1]["example_sentence"] = (
            "The reporter reviewed every small detail."
        )
        client = self.llm_client_with_payload(payload)

        result = generate_and_save_vocabulary(
            user=self.user,
            title="Zodiac",
            release_year=2007,
            item_count=5,
            client=client,
        )

        self.assertEqual(result.created_count, 3)
        self.assertEqual(result.cloze_ineligibility.missing_target, 1)
        client.chat.completions.create.assert_called_once()
        schema = client.chat.completions.create.call_args.kwargs[
            "response_format"
        ]["json_schema"]["schema"]["properties"]["items"]
        self.assertEqual((schema["minItems"], schema["maxItems"]), (1, 8))

    def test_candidate_without_cloze_sentence_is_still_saved(self):
        payload = vocabulary_payload()
        payload["items"][0]["example_sentence"] = (
            "The reporter reviewed every small detail."
        )
        client = self.llm_client_with_payload(payload)

        result = generate_and_save_vocabulary(
            user=self.user,
            title="Zodiac",
            release_year=2007,
            item_count=1,
            client=client,
        )

        self.assertEqual(result.created_count, 1)
        self.assertEqual(result.cloze_ineligibility.missing_target, 1)
        self.assertIsNone(VocabularyItem.objects.get().blank_sentence)

    def test_rejects_term_that_is_only_a_source_substring(self):
        client = self.llm_client_with_payload(vocabulary_payload())
        source = SourceDocument(
            text="They will overscrutinize the details.",
            format="script",
        )

        with self.assertRaises(VocabularyResponseError):
            generate_and_save_vocabulary(
                user=self.user,
                title="Zodiac",
                release_year=2007,
                item_count=5,
                client=client,
                source=source,
            )

        self.assertFalse(Movie.objects.exists())
        self.assertFalse(VocabularyItem.objects.exists())

    def test_rejects_empty_source_document_before_calling_provider(self):
        client = Mock()

        with self.assertRaisesRegex(ValueError, "non-empty SourceDocument"):
            generate_and_save_vocabulary(
                user=self.user,
                title="Zodiac",
                release_year=2007,
                client=client,
                source=SourceDocument(text=" ", format="script"),
            )

        client.chat.completions.create.assert_not_called()

    def test_provider_error_is_converted_and_writes_nothing(self):
        client = Mock()
        client.chat.completions.create.side_effect = RuntimeError("provider details")

        with self.assertRaises(VocabularyProviderError):
            generate_and_save_vocabulary(
                user=self.user, title="Zodiac", release_year=2007, client=client
            )

        self.assertFalse(Movie.objects.exists())

    def test_rejects_empty_response_without_writing(self):
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content=None),
                )
            ]
        )

        with self.assertRaises(VocabularyResponseError):
            generate_and_save_vocabulary(
                user=self.user,
                title="Zodiac",
                release_year=2007,
                client=client,
            )

        self.assertFalse(Movie.objects.exists())

    def test_rejects_empty_choices_without_writing(self):
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(choices=[])

        with self.assertRaises(VocabularyResponseError):
            generate_and_save_vocabulary(
                user=self.user,
                title="Zodiac",
                release_year=2007,
                client=client,
            )

        self.assertFalse(Movie.objects.exists())

    def test_rejects_truncated_response_without_writing(self):
        client = self.llm_client_with_payload(
            vocabulary_payload(), finish_reason="length"
        )

        with self.assertRaises(VocabularyResponseError):
            generate_and_save_vocabulary(
                user=self.user,
                title="Zodiac",
                release_year=2007,
                client=client,
            )

        self.assertFalse(Movie.objects.exists())

    def test_rejects_malformed_json_without_writing(self):
        client = Mock()
        client.chat.completions.create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content="{"),
                )
            ]
        )

        with self.assertRaises(VocabularyResponseError):
            generate_and_save_vocabulary(
                user=self.user,
                title="Zodiac",
                release_year=2007,
                client=client,
            )

        self.assertFalse(Movie.objects.exists())

    @override_settings(LLM_API_KEY="")
    def test_missing_api_key_fails_without_writing(self):
        with self.assertRaises(VocabularyConfigurationError):
            generate_and_save_vocabulary(
                user=self.user, title="Zodiac", release_year=2007
            )

        self.assertFalse(Movie.objects.exists())

    @override_settings(LLM_MODEL="")
    def test_missing_model_fails_without_writing(self):
        with self.assertRaises(VocabularyConfigurationError):
            generate_and_save_vocabulary(
                user=self.user, title="Zodiac", release_year=2007, client=Mock()
            )

        self.assertFalse(Movie.objects.exists())

    @override_settings(LLM_MAX_TOKENS_PARAMETER="unsupported")
    def test_invalid_token_parameter_fails_without_writing(self):
        with self.assertRaises(VocabularyConfigurationError):
            generate_and_save_vocabulary(
                user=self.user, title="Zodiac", release_year=2007, client=Mock()
            )

        self.assertFalse(Movie.objects.exists())

    @override_settings(
        LLM_API_KEY="generic-test-key",
        LLM_MODEL="vendor/model-name",
        LLM_BASE_URL="https://vendor.example/v1",
        LLM_TIMEOUT_SECONDS=18.5,
        LLM_TEMPERATURE="0",
        LLM_REASONING_EFFORT="none",
        LLM_MAX_TOKENS_PARAMETER="max_tokens",
    )
    @patch("openai.OpenAI")
    def test_builds_configured_generic_client_and_closes_it(self, client_type):
        provider_client = client_type.return_value
        provider_client.chat.completions.create.return_value = SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(
                        content=json.dumps(vocabulary_payload())
                    ),
                )
            ]
        )

        generate_and_save_vocabulary(
            user=self.user, title="Zodiac", release_year=2007
        )

        self.assertEqual(
            client_type.call_args.kwargs["base_url"],
            "https://vendor.example/v1",
        )
        self.assertEqual(client_type.call_args.kwargs["api_key"], "generic-test-key")
        self.assertEqual(client_type.call_args.kwargs["timeout"], 18.5)
        self.assertEqual(client_type.call_args.kwargs["max_retries"], 0)
        request_kwargs = provider_client.chat.completions.create.call_args.kwargs
        self.assertEqual(request_kwargs["model"], "vendor/model-name")
        self.assertEqual(request_kwargs["temperature"], 0.0)
        self.assertEqual(request_kwargs["reasoning_effort"], "none")
        provider_client.close.assert_called_once_with()

    @override_settings(
        LLM_TEMPERATURE="",
        LLM_REASONING_EFFORT="",
        LLM_MAX_TOKENS_PARAMETER="max_completion_tokens",
    )
    def test_optional_parameters_can_use_provider_defaults(self):
        client = self.llm_client_with_payload(vocabulary_payload())

        generate_and_save_vocabulary(
            user=self.user, title="Zodiac", release_year=2007, client=client
        )

        request_kwargs = client.chat.completions.create.call_args.kwargs
        self.assertIn("max_completion_tokens", request_kwargs)
        self.assertNotIn("max_tokens", request_kwargs)
        self.assertNotIn("temperature", request_kwargs)
        self.assertNotIn("reasoning_effort", request_kwargs)

    def test_database_failure_rolls_back_new_movie(self):
        parsed = VocabularyExtractionCandidate.model_validate(vocabulary_payload())
        client = self.llm_client_with_payload(parsed)

        with patch.object(
            VocabularyItem.objects, "bulk_create", side_effect=IntegrityError("boom")
        ), self.assertRaises(VocabularyPersistenceError):
            generate_and_save_vocabulary(
                user=self.user, title="Zodiac", release_year=2007, client=client
            )

        self.assertFalse(Movie.objects.exists())
        self.assertFalse(VocabularyItem.objects.exists())
