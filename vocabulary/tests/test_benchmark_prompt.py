import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from vocabulary.ingestion import SourceDocument
from vocabulary.providers import (
    CandidateSchemaRejections,
    VocabularyProviderResult,
)
from vocabulary.schemas import VocabularyItemCandidate, VocabularyItemResponse
from vocabulary.services import (
    CandidateRejections,
    ClozeIneligibility,
    VocabularyPromptBenchmarkResult,
    VocabularyResponseError,
    benchmark_vocabulary_prompt,
)


def candidate(
    word="scrutinize",
    *,
    example="The reviewer chose to scrutinize every detail.",
):
    return VocabularyItemCandidate.model_validate(
        {
            "word_or_phrase": word,
            "type": "verb",
            "CEFR_level": "C1",
            "definition_en": "To examine something very carefully.",
            "example_sentence": example,
        }
    )


def benchmark_result(*, movie_title="Inception", candidate_limit=50):
    item = VocabularyItemResponse.model_validate(
        {
            **candidate().model_dump(mode="json", by_alias=True),
            "blank_sentence": "The reviewer chose to ___ every detail.",
        }
    )
    return VocabularyPromptBenchmarkResult(
        movie_title=movie_title,
        candidate_limit=candidate_limit,
        provider_name="llm",
        provider_returned_count=3,
        schema_valid_count=2,
        items=(item,),
        rejections=CandidateRejections(duplicate=1, malformed=1),
        schema_rejections=CandidateSchemaRejections(invalid_type=1),
        cloze_ineligibility=ClozeIneligibility(),
    )


class FakeProvider:
    name = "fake-llm"

    def __init__(self, result):
        self.result = result
        self.calls = []
        self.closed = False

    def generate(self, **kwargs):
        self.calls.append(kwargs)
        return self.result

    def close(self):
        self.closed = True


class PromptBenchmarkServiceTests(SimpleTestCase):
    def test_runs_validation_and_grounding_without_database_access(self):
        provider = FakeProvider(
            VocabularyProviderResult(
                movie_title="Inception",
                items=(candidate(), candidate(" SCRUTINIZE ")),
                returned_count=3,
                schema_rejections=CandidateSchemaRejections(invalid_type=1),
            )
        )
        source = SourceDocument(
            text="The team scrutinized every detail.",
            format="script",
            filename="inception.txt",
            pre_filtered=True,
        )

        with patch(
            "vocabulary.services.build_vocabulary_llm_client",
            return_value=provider,
        ):
            result = benchmark_vocabulary_prompt(
                movie_title="  Inception  ",
                candidate_limit=12,
                source=source,
            )

        self.assertEqual(result.movie_title, "Inception")
        self.assertEqual(result.provider_name, "fake-llm")
        self.assertEqual(result.provider_returned_count, 3)
        self.assertEqual(result.schema_valid_count, 2)
        self.assertEqual(result.accepted_count, 1)
        self.assertEqual(result.rejected_count, 2)
        self.assertEqual(result.rejections.duplicate, 1)
        self.assertEqual(result.schema_rejections.invalid_type, 1)
        self.assertEqual(
            result.items[0].blank_sentence,
            "The reviewer chose to ___ every detail.",
        )
        self.assertEqual(provider.calls[0]["candidate_limit"], 12)
        self.assertEqual(provider.calls[0]["movie_reference"], "Inception")
        self.assertIs(provider.calls[0]["source"], source)
        self.assertTrue(provider.closed)

    def test_returns_zero_accepted_items_so_rejections_can_be_benchmarked(self):
        provider = FakeProvider(
            VocabularyProviderResult(
                movie_title="Inception",
                items=(candidate(),),
                returned_count=1,
                schema_rejections=CandidateSchemaRejections(),
            )
        )
        source = SourceDocument(
            text="No matching vocabulary occurs here.",
            format="script",
        )

        with patch(
            "vocabulary.services.build_vocabulary_llm_client",
            return_value=provider,
        ):
            result = benchmark_vocabulary_prompt(
                movie_title="Inception",
                source=source,
            )

        self.assertEqual(result.items, ())
        self.assertEqual(result.rejections.ungrounded, 1)
        self.assertEqual(result.rejected_count, 1)
        self.assertTrue(provider.closed)

    def test_rejects_wrong_movie_and_always_closes_provider(self):
        provider = FakeProvider(
            VocabularyProviderResult(
                movie_title="Arrival",
                items=(candidate(),),
                returned_count=1,
                schema_rejections=CandidateSchemaRejections(),
            )
        )

        with patch(
            "vocabulary.services.build_vocabulary_llm_client",
            return_value=provider,
        ), self.assertRaises(VocabularyResponseError):
            benchmark_vocabulary_prompt(movie_title="Inception")

        self.assertTrue(provider.closed)


@override_settings(LLM_MODEL="benchmark-test-model")
class BenchmarkPromptCommandTests(SimpleTestCase):
    @patch("vocabulary.management.commands.benchmark_prompt.benchmark_vocabulary_prompt")
    def test_pretty_prints_json_to_stdout_with_default_limit(self, benchmark):
        benchmark.return_value = benchmark_result()
        stdout = StringIO()

        call_command(
            "benchmark_prompt",
            movie="  Inception  ",
            stdout=stdout,
        )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["schema_version"], 1)
        self.assertEqual(payload["movie_title"], "Inception")
        self.assertEqual(payload["candidate_limit"], 50)
        self.assertEqual(payload["prompt"]["model"], "benchmark-test-model")
        self.assertEqual(payload["source"], None)
        self.assertEqual(payload["counts"]["accepted"], 1)
        self.assertEqual(payload["counts"]["rejected"], 2)
        self.assertEqual(payload["rejections"]["duplicate"], 1)
        self.assertEqual(payload["rejections"]["malformed"], 1)
        self.assertEqual(payload["rejections"]["schema"]["invalid_type"], 1)
        self.assertEqual(payload["items"][0]["word_or_phrase"], "scrutinize")
        benchmark.assert_called_once_with(
            movie_title="Inception",
            candidate_limit=50,
            source=None,
        )

    @patch("vocabulary.management.commands.benchmark_prompt.benchmark_vocabulary_prompt")
    def test_writes_json_and_short_summary_to_nested_output_path(self, benchmark):
        benchmark.return_value = benchmark_result(candidate_limit=10)
        stdout = StringIO()

        with TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "benchmarks" / "v1.json"
            destination.parent.mkdir(parents=True)
            destination.write_text("old benchmark", encoding="utf-8")
            call_command(
                "benchmark_prompt",
                movie="Inception",
                limit=10,
                output=destination,
                stdout=stdout,
            )
            payload = json.loads(destination.read_text(encoding="utf-8"))

        self.assertEqual(payload["candidate_limit"], 10)
        self.assertIn("1 accepted, 2 rejected", stdout.getvalue())
        self.assertIn("v1.json", stdout.getvalue())
        self.assertNotIn('"items"', stdout.getvalue())

    @patch("vocabulary.management.commands.benchmark_prompt.benchmark_vocabulary_prompt")
    def test_parses_and_prefilters_local_source_file(self, benchmark):
        benchmark.return_value = benchmark_result(candidate_limit=10)

        with TemporaryDirectory() as temporary_directory:
            source_path = Path(temporary_directory) / "inception.txt"
            destination = Path(temporary_directory) / "result.json"
            source_path.write_text(
                "Hello there.\nWe must scrutinize every detail before deciding.",
                encoding="utf-8",
            )
            call_command(
                "benchmark_prompt",
                movie="Inception",
                limit=10,
                source_file=source_path,
                output=destination,
                stdout=StringIO(),
            )
            payload = json.loads(destination.read_text(encoding="utf-8"))

        prepared = benchmark.call_args.kwargs["source"]
        self.assertTrue(prepared.pre_filtered)
        self.assertIn("scrutinize", prepared.text)
        self.assertNotIn("Hello there", prepared.text)
        self.assertEqual(payload["source"]["filename"], "inception.txt")
        self.assertEqual(payload["source"]["format"], "script")
        self.assertTrue(payload["source"]["pre_filtered"])
        self.assertEqual(len(payload["source"]["prompt_sha256"]), 64)

    @patch("vocabulary.management.commands.benchmark_prompt.benchmark_vocabulary_prompt")
    def test_rejects_invalid_limit_before_calling_provider(self, benchmark):
        with self.assertRaisesMessage(CommandError, "--limit must be between"):
            call_command(
                "benchmark_prompt",
                movie="Inception",
                limit=0,
            )

        benchmark.assert_not_called()

    @patch("vocabulary.management.commands.benchmark_prompt.benchmark_vocabulary_prompt")
    def test_output_cannot_overwrite_source_file(self, benchmark):
        with TemporaryDirectory() as temporary_directory:
            source_path = Path(temporary_directory) / "inception.txt"
            source_path.write_text("We must scrutinize every detail.", encoding="utf-8")

            with self.assertRaisesMessage(CommandError, "cannot overwrite"):
                call_command(
                    "benchmark_prompt",
                    movie="Inception",
                    source_file=source_path,
                    output=source_path,
                )

        benchmark.assert_not_called()
