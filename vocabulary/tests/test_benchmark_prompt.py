import json
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import SimpleTestCase, override_settings

from vocabulary.ingestion import SourceDocument
from vocabulary.management.commands.benchmark_prompt import Command
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
from vocabulary.source_acquisition import AcquiredSource, SourceNotFoundError


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


def benchmark_result(
    *,
    movie_title="Inception",
    release_year=None,
    candidate_limit=50,
):
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
        release_year=release_year,
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
                release_year=2010,
                candidate_limit=12,
                source=source,
            )

        self.assertEqual(result.movie_title, "Inception")
        self.assertEqual(result.release_year, 2010)
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
        self.assertEqual(provider.calls[0]["movie_reference"], "Inception (2010)")
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

    def test_rejects_invalid_release_year_before_building_provider(self):
        with patch(
            "vocabulary.services.build_vocabulary_llm_client"
        ) as build_provider, self.assertRaisesMessage(
            ValueError,
            "release_year must be between",
        ):
            benchmark_vocabulary_prompt(
                movie_title="Inception",
                release_year=1800,
            )

        build_provider.assert_not_called()


@override_settings(
    LLM_MODEL="benchmark-test-model",
    VOCABULARY_AUTO_SOURCE_PROVIDER="",
)
class BenchmarkPromptCommandTests(SimpleTestCase):
    def test_help_shows_only_benchmark_options(self):
        parser = Command().create_parser("manage.py", "benchmark_prompt")

        help_text = parser.format_help()

        self.assertIn("-m TITLE, --movie TITLE", help_text)
        self.assertIn("-y YEAR, --year YEAR", help_text)
        self.assertIn("-l COUNT, --limit COUNT", help_text)
        self.assertIn("-o PATH, --output PATH", help_text)
        self.assertIn("-f PATH, --source-file PATH", help_text)
        self.assertIn("--words-only", help_text)
        self.assertNotIn("--items-only", help_text)
        for hidden_option in (
            "--version",
            "--verbosity",
            "--settings",
            "--pythonpath",
            "--traceback",
            "--no-color",
            "--force-color",
            "--skip-checks",
        ):
            self.assertNotIn(hidden_option, help_text)

    @patch("vocabulary.management.commands.benchmark_prompt.benchmark_vocabulary_prompt")
    def test_pretty_prints_json_to_stdout_with_default_limit(self, benchmark):
        benchmark.return_value = benchmark_result()
        stdout = StringIO()

        call_command(
            "benchmark_prompt",
            movie="  Inception  ",
            stdout=stdout,
            stderr=StringIO(),
        )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["schema_version"], 2)
        self.assertEqual(payload["movie_title"], "Inception")
        self.assertIsNone(payload["release_year"])
        self.assertEqual(payload["candidate_limit"], 50)
        self.assertEqual(payload["prompt"]["model"], "benchmark-test-model")
        self.assertEqual(payload["source"]["origin"], "model_knowledge")
        self.assertEqual(payload["source"]["status"], "not_configured")
        self.assertEqual(payload["counts"]["accepted"], 1)
        self.assertEqual(payload["counts"]["rejected"], 2)
        self.assertEqual(payload["rejections"]["duplicate"], 1)
        self.assertEqual(payload["rejections"]["malformed"], 1)
        self.assertEqual(payload["rejections"]["schema"]["invalid_type"], 1)
        self.assertEqual(payload["items"][0]["word_or_phrase"], "scrutinize")
        benchmark.assert_called_once_with(
            movie_title="Inception",
            release_year=None,
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
                stderr=StringIO(),
            )
            payload = json.loads(destination.read_text(encoding="utf-8"))

        self.assertEqual(payload["candidate_limit"], 10)
        self.assertIn("1 accepted, 2 rejected", stdout.getvalue())
        self.assertIn("v1.json", stdout.getvalue())
        self.assertNotIn('"items"', stdout.getvalue())

    @patch("vocabulary.management.commands.benchmark_prompt.benchmark_vocabulary_prompt")
    def test_words_only_prints_bare_items_array(self, benchmark):
        benchmark.return_value = benchmark_result()
        stdout = StringIO()
        stderr = StringIO()

        call_command(
            "benchmark_prompt",
            movie="Inception",
            words_only=True,
            stdout=stdout,
            stderr=stderr,
        )

        payload = json.loads(stdout.getvalue())
        self.assertIsInstance(payload, list)
        self.assertEqual(len(payload), 1)
        self.assertEqual(payload[0]["word_or_phrase"], "scrutinize")
        self.assertNotIn("schema_version", payload[0])
        self.assertIn("using model knowledge", stderr.getvalue())

    @patch("vocabulary.management.commands.benchmark_prompt.benchmark_vocabulary_prompt")
    def test_words_only_writes_bare_items_array(self, benchmark):
        benchmark.return_value = benchmark_result()
        stdout = StringIO()

        with TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "items.json"
            call_command(
                "benchmark_prompt",
                movie="Inception",
                words_only=True,
                output=destination,
                stdout=stdout,
                stderr=StringIO(),
            )
            payload = json.loads(destination.read_text(encoding="utf-8"))

        self.assertIsInstance(payload, list)
        self.assertEqual(payload[0]["word_or_phrase"], "scrutinize")
        self.assertIn("1 accepted, 2 rejected", stdout.getvalue())

    @patch("vocabulary.management.commands.benchmark_prompt.acquire_automatic_source")
    @patch("vocabulary.management.commands.benchmark_prompt.benchmark_vocabulary_prompt")
    def test_parses_and_prefilters_local_source_file(
        self,
        benchmark,
        acquire_source,
    ):
        benchmark.return_value = benchmark_result(candidate_limit=10)
        stderr = StringIO()

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
                stderr=stderr,
            )
            payload = json.loads(destination.read_text(encoding="utf-8"))

        prepared = benchmark.call_args.kwargs["source"]
        self.assertTrue(prepared.pre_filtered)
        self.assertIn("scrutinize", prepared.text)
        self.assertNotIn("Hello there", prepared.text)
        self.assertEqual(payload["source"]["origin"], "local_file")
        self.assertEqual(payload["source"]["status"], "used")
        self.assertEqual(payload["source"]["filename"], "inception.txt")
        self.assertEqual(payload["source"]["format"], "script")
        self.assertTrue(payload["source"]["pre_filtered"])
        self.assertEqual(len(payload["source"]["prompt_sha256"]), 64)
        self.assertIn("pre-filtered in memory", stderr.getvalue())
        acquire_source.assert_not_called()

    @override_settings(VOCABULARY_AUTO_SOURCE_PROVIDER="opensubtitles")
    @patch("vocabulary.management.commands.benchmark_prompt.acquire_automatic_source")
    @patch("vocabulary.management.commands.benchmark_prompt.benchmark_vocabulary_prompt")
    def test_acquires_and_reports_automatic_subtitles(
        self,
        benchmark,
        acquire_source,
    ):
        benchmark.return_value = benchmark_result(
            movie_title="Zodiac",
            release_year=2007,
            candidate_limit=10,
        )
        acquire_source.return_value = AcquiredSource(
            document=SourceDocument(
                text="We must scrutinize every detail before deciding.",
                format="srt",
                filename="Zodiac.2007.srt",
            ),
            provider="OpenSubtitles",
            source_id="123",
            imdb_id="443706",
        )
        stderr = StringIO()

        with TemporaryDirectory() as temporary_directory:
            destination = Path(temporary_directory) / "zodiac.json"
            call_command(
                "benchmark_prompt",
                movie="Zodiac",
                year=2007,
                limit=10,
                output=destination,
                stdout=StringIO(),
                stderr=stderr,
            )
            payload = json.loads(destination.read_text(encoding="utf-8"))

        acquire_source.assert_called_once_with(
            title="Zodiac",
            release_year=2007,
        )
        prepared = benchmark.call_args.kwargs["source"]
        self.assertTrue(prepared.pre_filtered)
        self.assertIn("scrutinize", prepared.text)
        self.assertEqual(benchmark.call_args.kwargs["release_year"], 2007)
        self.assertEqual(payload["release_year"], 2007)
        self.assertEqual(payload["source"]["origin"], "automatic")
        self.assertEqual(payload["source"]["status"], "used")
        self.assertEqual(payload["source"]["provider"], "OpenSubtitles")
        self.assertEqual(payload["source"]["source_id"], "123")
        self.assertEqual(payload["source"]["imdb_id"], "443706")
        self.assertIn("IMDb tt0443706", stderr.getvalue())
        self.assertIn("pre-filtered in memory", stderr.getvalue())
        self.assertNotIn("cached", stderr.getvalue().casefold())

    @override_settings(VOCABULARY_AUTO_SOURCE_PROVIDER="opensubtitles")
    @patch("vocabulary.management.commands.benchmark_prompt.acquire_automatic_source")
    @patch("vocabulary.management.commands.benchmark_prompt.benchmark_vocabulary_prompt")
    def test_reports_automatic_source_fallback_to_model_knowledge(
        self,
        benchmark,
        acquire_source,
    ):
        benchmark.return_value = benchmark_result(release_year=2010)
        acquire_source.side_effect = SourceNotFoundError(
            "No matching English subtitles were found automatically."
        )
        stdout = StringIO()
        stderr = StringIO()

        call_command(
            "benchmark_prompt",
            movie="Inception",
            year=2010,
            stdout=stdout,
            stderr=stderr,
        )

        payload = json.loads(stdout.getvalue())
        self.assertEqual(payload["source"]["origin"], "automatic")
        self.assertEqual(payload["source"]["status"], "unavailable")
        self.assertEqual(payload["source"]["provider"], "OpenSubtitles")
        self.assertIn("Using model knowledge", payload["source"]["note"])
        self.assertIn("Using model knowledge", stderr.getvalue())
        self.assertIsNone(benchmark.call_args.kwargs["source"])

    @patch("vocabulary.management.commands.benchmark_prompt.benchmark_vocabulary_prompt")
    def test_rejects_invalid_limit_before_calling_provider(self, benchmark):
        with self.assertRaisesMessage(CommandError, "--limit must be between"):
            call_command(
                "benchmark_prompt",
                movie="Inception",
                limit=0,
            )

        benchmark.assert_not_called()

    @patch("vocabulary.management.commands.benchmark_prompt.acquire_automatic_source")
    @patch("vocabulary.management.commands.benchmark_prompt.benchmark_vocabulary_prompt")
    def test_rejects_invalid_year_before_acquiring_source(
        self,
        benchmark,
        acquire_source,
    ):
        with self.assertRaisesMessage(CommandError, "--year must be between"):
            call_command(
                "benchmark_prompt",
                movie="Inception",
                year=1800,
            )

        acquire_source.assert_not_called()
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
