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
    VocabularyExtractionResponse,
)
from vocabulary.services import (
    VocabularyConfigurationError,
    VocabularyPersistenceError,
    VocabularyProviderError,
    VocabularyResponseError,
    generate_and_save_vocabulary,
)

from .factories import vocabulary_payload


@override_settings(VOCABULARY_LLM_PROVIDER="openai")
class VocabularyGenerationServiceTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.user = get_user_model().objects.create_user(
            username="learner", password="test-pass-42"
        )

    def openai_client_with_payload(self, payload):
        client = Mock()
        client.responses.parse.return_value = SimpleNamespace(output_parsed=payload)
        return client

    def gemini_client_with_payload(self, payload):
        client = Mock()
        client.models.generate_content.return_value = SimpleNamespace(
            text=json.dumps(payload)
        )
        return client

    def fireworks_client_with_payload(self, payload, *, finish_reason="stop"):
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

    @override_settings(
        OPENAI_MODEL="test-structured-model",
        GEMINI_API_KEY="",
    )
    def test_creates_movie_and_validated_items_atomically(self):
        parsed = VocabularyExtractionCandidate.model_validate(vocabulary_payload())
        client = self.openai_client_with_payload(parsed)

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
        request_kwargs = client.responses.parse.call_args.kwargs
        self.assertEqual(request_kwargs["model"], "test-structured-model")
        self.assertIs(request_kwargs["text_format"], VocabularyExtractionCandidate)

    def test_accepts_and_persists_one_hundred_items(self):
        parsed = VocabularyExtractionCandidate.model_validate(
            vocabulary_payload(item_count=100)
        )
        client = self.openai_client_with_payload(parsed)

        result = generate_and_save_vocabulary(
            user=self.user,
            title="Zodiac",
            release_year=2007,
            item_count=100,
            client=client,
        )

        self.assertEqual(result.created_count, 100)
        self.assertEqual(VocabularyItem.objects.count(), 100)
        prompt = client.responses.parse.call_args.kwargs["input"][1]["content"]
        self.assertIn('"requested_items": 115', prompt)

    def test_accepts_and_persists_b1_backfill_item(self):
        payload = vocabulary_payload(word="abandon")
        payload["items"][0]["CEFR_level"] = "B1"
        client = self.openai_client_with_payload(payload)

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
        client = self.openai_client_with_payload(vocabulary_payload(item_count=18))

        result = generate_and_save_vocabulary(
            user=self.user,
            title="Zodiac",
            release_year=2007,
            item_count=2,
            client=client,
        )

        self.assertEqual(result.created_count, 2)
        self.assertEqual(VocabularyItem.objects.count(), 2)
        prompt = client.responses.parse.call_args.kwargs["input"][1]["content"]
        self.assertIn('"requested_items": 17', prompt)

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

    @override_settings(
        VOCABULARY_LLM_PROVIDER="gemini",
        GEMINI_MODEL="gemini-test-structured-model",
    )
    def test_gemini_creates_validated_items_with_structured_output(self):
        client = self.gemini_client_with_payload(vocabulary_payload())

        result = generate_and_save_vocabulary(
            user=self.user,
            title="Zodiac",
            release_year=2007,
            item_count=5,
            client=client,
        )

        self.assertTrue(result.movie_created)
        self.assertEqual(result.created_count, 1)
        request_kwargs = client.models.generate_content.call_args.kwargs
        self.assertEqual(request_kwargs["model"], "gemini-test-structured-model")
        self.assertIn('"movie_reference": "Zodiac (2007)"', request_kwargs["contents"])
        config = request_kwargs["config"]
        self.assertIn("Strictly exclude A1-A2 vocabulary", config.system_instruction)
        self.assertIn("Quality outranks count.", config.system_instruction)
        self.assertIn(
            '"mental projection" is not advanced merely because its concept sounds complex',
            config.system_instruction,
        )
        self.assertIn("Verbatim source grounding is non-negotiable", config.system_instruction)
        self.assertEqual(config.response_mime_type, "application/json")
        item_schema = config.response_json_schema["$defs"]["VocabularyItemCandidate"]
        self.assertIn("CEFR_level", item_schema["properties"])

    @override_settings(
        VOCABULARY_LLM_PROVIDER="fireworks",
        FIREWORKS_MODEL="accounts/fireworks/models/deepseek-test",
    )
    def test_fireworks_creates_validated_items_with_structured_output(self):
        client = self.fireworks_client_with_payload(vocabulary_payload(item_count=20))

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
        self.assertEqual(
            request_kwargs["model"], "accounts/fireworks/models/deepseek-test"
        )
        self.assertEqual(request_kwargs["max_tokens"], 3_712)
        self.assertEqual(request_kwargs["reasoning_effort"], "none")
        self.assertNotIn("extra_body", request_kwargs)
        self.assertIn(
            "Strictly exclude A1-A2 vocabulary",
            request_kwargs["messages"][0]["content"],
        )
        self.assertIn(
            "NEVER invent, hallucinate",
            request_kwargs["messages"][0]["content"],
        )
        self.assertIn(
            "backfill the remaining slots with genuine B1 entries",
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
        self.assertEqual(collection_schema["minItems"], 20)
        self.assertEqual(collection_schema["maxItems"], 20)
        self.assertIn("CEFR_level", item_schema["properties"])
        self.assertNotIn("blank_sentence", item_schema["properties"])
        client.close.assert_not_called()

    @override_settings(
        VOCABULARY_LLM_PROVIDER="fireworks",
        FIREWORKS_MODEL="accounts/fireworks/models/deepseek-test",
    )
    def test_fireworks_scales_output_limit_for_thirty_items(self):
        client = self.fireworks_client_with_payload(vocabulary_payload(item_count=45))

        generate_and_save_vocabulary(
            user=self.user,
            title="Zodiac",
            release_year=2007,
            item_count=30,
            client=client,
        )

        request_kwargs = client.chat.completions.create.call_args.kwargs
        self.assertEqual(request_kwargs["max_tokens"], 7_712)

    @override_settings(
        VOCABULARY_LLM_PROVIDER="fireworks",
        FIREWORKS_MODEL="accounts/fireworks/models/deepseek-test",
    )
    def test_fireworks_logs_usage_without_response_content(self):
        client = self.fireworks_client_with_payload(vocabulary_payload(item_count=20))

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
        self.assertIn("reasoning_effort=none", usage_log)
        self.assertNotIn("scrutinize", usage_log)

    @override_settings(
        VOCABULARY_LLM_PROVIDER="fireworks",
        FIREWORKS_MODEL="accounts/fireworks/models/deepseek-contract-test",
    )
    def test_fireworks_openai_compatibility_transport_contract(self):
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
                    "model": "accounts/fireworks/models/deepseek-contract-test",
                    "choices": [
                        {
                            "index": 0,
                            "message": {
                                "role": "assistant",
                                "content": json.dumps(vocabulary_payload(item_count=20)),
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
            api_key="fireworks-contract-test-key",
            base_url="https://api.fireworks.ai/inference/v1",
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
            "https://api.fireworks.ai/inference/v1/chat/completions",
        )
        body = captured_request["body"]
        self.assertEqual(body["max_tokens"], 3_712)
        self.assertEqual(body["reasoning_effort"], "none")
        self.assertNotIn("context_length_exceeded_behavior", body)
        self.assertEqual(body["response_format"]["type"], "json_schema")
        items_schema = body["response_format"]["json_schema"]["schema"][
            "properties"
        ]["items"]
        self.assertEqual(items_schema["minItems"], 20)
        self.assertEqual(items_schema["maxItems"], 20)
        self.assertIn(
            "CEFR_level",
            body["response_format"]["json_schema"]["schema"]["$defs"][
                "VocabularyItemCandidate"
            ]["properties"],
        )

    def test_reuses_owned_movie_and_skips_existing_term(self):
        parsed = VocabularyExtractionCandidate.model_validate(vocabulary_payload())
        client = self.openai_client_with_payload(parsed)
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
        client = self.openai_client_with_payload(payload)

        with self.assertRaises(VocabularyResponseError):
            generate_and_save_vocabulary(
                user=self.user, title="Zodiac", release_year=2007, client=client
            )

        self.assertFalse(Movie.objects.exists())
        self.assertFalse(VocabularyItem.objects.exists())

    def test_source_is_sent_as_untrusted_evidence_and_grounded_items_are_saved(self):
        parsed = VocabularyExtractionCandidate.model_validate(vocabulary_payload())
        client = self.openai_client_with_payload(parsed)
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
        user_prompt = client.responses.parse.call_args.kwargs["input"][1]["content"]
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
        client = self.openai_client_with_payload(payload)

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
        client = self.openai_client_with_payload(payload)

        with self.assertRaises(VocabularyResponseError):
            generate_and_save_vocabulary(
                user=self.user,
                title="Zodiac",
                release_year=2007,
                item_count=5,
                client=client,
            )

        self.assertFalse(VocabularyItem.objects.exists())

    def test_surplus_absorbs_rejections_without_requesting_a_refill(self):
        payload = vocabulary_payload(item_count=17)
        payload["items"][1]["example_sentence"] = (
            "The reporter scrutinized every small detail."
        )
        client = self.openai_client_with_payload(payload)

        result = generate_and_save_vocabulary(
            user=self.user,
            title="Zodiac",
            release_year=2007,
            item_count=2,
            client=client,
        )

        self.assertEqual(result.created_count, 2)
        client.responses.parse.assert_called_once()

    @override_settings(VOCABULARY_LLM_PROVIDER="fireworks")
    def test_fireworks_surplus_absorbs_rejection_without_refill(self):
        payload = vocabulary_payload(item_count=17)
        payload["items"][1]["example_sentence"] = (
            "The reporter scrutinized every small detail."
        )
        client = self.fireworks_client_with_payload(payload)

        result = generate_and_save_vocabulary(
            user=self.user,
            title="Zodiac",
            release_year=2007,
            item_count=2,
            client=client,
        )

        self.assertEqual(result.created_count, 2)
        client.chat.completions.create.assert_called_once()

    def test_single_shot_saves_valid_shortfall_without_refill(self):
        payload = vocabulary_payload(item_count=51)
        payload["items"][-1]["example_sentence"] = (
            "The reporter reviewed every small detail."
        )
        client = self.openai_client_with_payload(payload)

        with self.assertLogs("vocabulary.usage", level="INFO") as logs:
            result = generate_and_save_vocabulary(
                user=self.user,
                title="Zodiac",
                release_year=2007,
                item_count=80,
                client=client,
            )

        self.assertEqual(result.created_count, 50)
        self.assertEqual(VocabularyItem.objects.count(), 50)
        client.responses.parse.assert_called_once()
        yield_log = "\n".join(logs.output)
        self.assertIn("initial_returned=51", yield_log)
        self.assertIn("initial_accepted=50", yield_log)
        self.assertIn("initial_rejected=1", yield_log)
        self.assertIn("final_accepted=50", yield_log)
        self.assertNotIn("refill", yield_log)
        self.assertIn(
            "rejection_reasons=duplicate:0,ungrounded:0,invalid_example:1",
            yield_log,
        )

    @override_settings(VOCABULARY_LLM_PROVIDER="fireworks")
    def test_fireworks_underfilled_response_remains_single_shot(self):
        payload = vocabulary_payload(item_count=3)
        payload["items"][-1]["example_sentence"] = (
            "The reporter reviewed every small detail."
        )
        client = self.fireworks_client_with_payload(payload)

        result = generate_and_save_vocabulary(
            user=self.user,
            title="Zodiac",
            release_year=2007,
            item_count=5,
            client=client,
        )

        self.assertEqual(result.created_count, 2)
        client.chat.completions.create.assert_called_once()
        schema = client.chat.completions.create.call_args.kwargs[
            "response_format"
        ]["json_schema"]["schema"]["properties"]["items"]
        self.assertEqual((schema["minItems"], schema["maxItems"]), (20, 20))

    def test_all_semantically_invalid_candidates_write_nothing(self):
        payload = vocabulary_payload()
        payload["items"][0]["example_sentence"] = (
            "The reporter scrutinized every small detail."
        )
        client = self.openai_client_with_payload(payload)

        with self.assertRaises(VocabularyResponseError):
            generate_and_save_vocabulary(
                user=self.user,
                title="Zodiac",
                release_year=2007,
                item_count=1,
                client=client,
            )

        self.assertFalse(Movie.objects.exists())
        self.assertFalse(VocabularyItem.objects.exists())

    def test_rejects_term_that_is_only_a_source_substring(self):
        client = self.openai_client_with_payload(vocabulary_payload())
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

        client.responses.parse.assert_not_called()

    def test_provider_error_is_converted_and_writes_nothing(self):
        client = Mock()
        client.responses.parse.side_effect = RuntimeError("provider details")

        with self.assertRaises(VocabularyProviderError):
            generate_and_save_vocabulary(
                user=self.user, title="Zodiac", release_year=2007, client=client
            )

        self.assertFalse(Movie.objects.exists())

    @override_settings(VOCABULARY_LLM_PROVIDER="gemini")
    def test_gemini_provider_error_is_converted_and_writes_nothing(self):
        client = Mock()
        client.models.generate_content.side_effect = RuntimeError("provider details")

        with self.assertRaises(VocabularyProviderError):
            generate_and_save_vocabulary(
                user=self.user,
                title="Zodiac",
                release_year=2007,
                client=client,
            )

        self.assertFalse(Movie.objects.exists())

    @override_settings(VOCABULARY_LLM_PROVIDER="gemini")
    def test_gemini_rejects_empty_response_without_writing(self):
        client = Mock()
        client.models.generate_content.return_value = SimpleNamespace(text=None)

        with self.assertRaises(VocabularyResponseError):
            generate_and_save_vocabulary(
                user=self.user,
                title="Zodiac",
                release_year=2007,
                client=client,
            )

        self.assertFalse(Movie.objects.exists())

    @override_settings(VOCABULARY_LLM_PROVIDER="gemini")
    def test_gemini_rejects_malformed_json_without_writing(self):
        client = Mock()
        client.models.generate_content.return_value = SimpleNamespace(text="{")

        with self.assertRaises(VocabularyResponseError):
            generate_and_save_vocabulary(
                user=self.user,
                title="Zodiac",
                release_year=2007,
                client=client,
            )

        self.assertFalse(Movie.objects.exists())

    @override_settings(VOCABULARY_LLM_PROVIDER="fireworks")
    def test_fireworks_provider_error_is_converted_and_writes_nothing(self):
        client = Mock()
        client.chat.completions.create.side_effect = RuntimeError("provider details")

        with self.assertRaises(VocabularyProviderError):
            generate_and_save_vocabulary(
                user=self.user,
                title="Zodiac",
                release_year=2007,
                client=client,
            )

        self.assertFalse(Movie.objects.exists())

    @override_settings(VOCABULARY_LLM_PROVIDER="fireworks")
    def test_fireworks_rejects_empty_response_without_writing(self):
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

    @override_settings(VOCABULARY_LLM_PROVIDER="fireworks")
    def test_fireworks_rejects_empty_choices_without_writing(self):
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

    @override_settings(VOCABULARY_LLM_PROVIDER="fireworks")
    def test_fireworks_rejects_truncated_response_without_writing(self):
        client = self.fireworks_client_with_payload(
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

    @override_settings(VOCABULARY_LLM_PROVIDER="fireworks")
    def test_fireworks_rejects_malformed_json_without_writing(self):
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

    @override_settings(OPENAI_API_KEY="")
    def test_missing_api_key_fails_without_writing(self):
        with self.assertRaises(VocabularyConfigurationError):
            generate_and_save_vocabulary(
                user=self.user, title="Zodiac", release_year=2007
            )

        self.assertFalse(Movie.objects.exists())

    @override_settings(VOCABULARY_LLM_PROVIDER="gemini", GEMINI_API_KEY="")
    def test_missing_selected_gemini_key_fails_without_writing(self):
        with self.assertRaises(VocabularyConfigurationError):
            generate_and_save_vocabulary(
                user=self.user, title="Zodiac", release_year=2007
            )

        self.assertFalse(Movie.objects.exists())

    @override_settings(
        VOCABULARY_LLM_PROVIDER="fireworks",
        FIREWORKS_API_KEY="",
    )
    def test_missing_selected_fireworks_key_fails_without_writing(self):
        with self.assertRaises(VocabularyConfigurationError):
            generate_and_save_vocabulary(
                user=self.user, title="Zodiac", release_year=2007
            )

        self.assertFalse(Movie.objects.exists())

    @override_settings(VOCABULARY_LLM_PROVIDER="unsupported")
    def test_unknown_provider_fails_without_writing(self):
        with self.assertRaises(VocabularyConfigurationError):
            generate_and_save_vocabulary(
                user=self.user,
                title="Zodiac",
                release_year=2007,
                client=Mock(),
            )

        self.assertFalse(Movie.objects.exists())

    @override_settings(
        VOCABULARY_LLM_PROVIDER="gemini",
        GEMINI_API_KEY="gemini-test-key",
        GEMINI_TIMEOUT_SECONDS=12.5,
        OPENAI_API_KEY="",
    )
    @patch("google.genai.Client")
    def test_gemini_builds_and_closes_owned_client(self, client_type):
        provider_client = client_type.return_value
        provider_client.models.generate_content.return_value = SimpleNamespace(
            text=json.dumps(vocabulary_payload())
        )

        generate_and_save_vocabulary(
            user=self.user, title="Zodiac", release_year=2007
        )

        http_options = client_type.call_args.kwargs["http_options"]
        self.assertEqual(http_options.timeout, 12_500)
        provider_client.close.assert_called_once_with()

    @override_settings(
        VOCABULARY_LLM_PROVIDER="fireworks",
        FIREWORKS_API_KEY="fireworks-test-key",
        FIREWORKS_TIMEOUT_SECONDS=18.5,
        OPENAI_API_KEY="",
        GEMINI_API_KEY="",
    )
    @patch("openai.OpenAI")
    def test_fireworks_builds_and_closes_owned_client(self, client_type):
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
            "https://api.fireworks.ai/inference/v1",
        )
        self.assertEqual(client_type.call_args.kwargs["timeout"], 18.5)
        self.assertEqual(client_type.call_args.kwargs["max_retries"], 0)
        provider_client.close.assert_called_once_with()

    def test_database_failure_rolls_back_new_movie(self):
        parsed = VocabularyExtractionCandidate.model_validate(vocabulary_payload())
        client = self.openai_client_with_payload(parsed)

        with patch.object(
            VocabularyItem.objects, "bulk_create", side_effect=IntegrityError("boom")
        ), self.assertRaises(VocabularyPersistenceError):
            generate_and_save_vocabulary(
                user=self.user, title="Zodiac", release_year=2007, client=client
            )

        self.assertFalse(Movie.objects.exists())
        self.assertFalse(VocabularyItem.objects.exists())
