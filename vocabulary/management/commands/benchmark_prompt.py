import hashlib
import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path

from django.conf import settings
from django.core.management.base import (
    BaseCommand,
    CommandError,
    DjangoHelpFormatter,
)

from movies.models import current_year
from vocabulary.constants import MAX_GENERATION_CANDIDATES
from vocabulary.ingestion import (
    SourceDocument,
    SourceIngestionError,
    parse_local_source,
)
from vocabulary.providers import SYSTEM_PROMPT
from vocabulary.services import (
    VocabularyGenerationError,
    VocabularyPromptBenchmarkResult,
    benchmark_vocabulary_prompt,
    prepare_benchmark_source,
)
from vocabulary.source_acquisition import (
    SourceAcquisitionError,
    acquire_automatic_source,
)
from vocabulary.subtitle_filter import SubtitleFilterConfigurationError


class BenchmarkHelpFormatter(DjangoHelpFormatter):
    def __init__(self, *args, **kwargs):
        kwargs["max_help_position"] = 32
        super().__init__(*args, **kwargs)


def _source_metadata(
    *,
    original: SourceDocument,
    prepared: SourceDocument,
    origin: str,
    status: str,
    note: str,
    provider: str | None = None,
    source_id: str | None = None,
    imdb_id: str | None = None,
) -> dict[str, object]:
    return {
        "origin": origin,
        "status": status,
        "provider": provider,
        "source_id": source_id,
        "imdb_id": imdb_id,
        "filename": original.filename,
        "format": original.format,
        "input_characters": len(original.text),
        "prompt_characters": len(prepared.text),
        "prompt_sha256": hashlib.sha256(prepared.text.encode("utf-8")).hexdigest(),
        "pre_filtered": prepared.pre_filtered,
        "note": note,
    }


def _source_status_metadata(
    *,
    origin: str,
    status: str,
    note: str,
    provider: str | None = None,
    source_id: str | None = None,
    imdb_id: str | None = None,
) -> dict[str, object]:
    return {
        "origin": origin,
        "status": status,
        "provider": provider,
        "source_id": source_id,
        "imdb_id": imdb_id,
        "note": note,
    }


def _configured_source_provider() -> str | None:
    provider = getattr(settings, "VOCABULARY_AUTO_SOURCE_PROVIDER", "")
    if isinstance(provider, str) and provider.strip().casefold() == "opensubtitles":
        return "OpenSubtitles"
    return None


def _benchmark_payload(
    result: VocabularyPromptBenchmarkResult,
    *,
    source: dict[str, object] | None,
) -> dict[str, object]:
    schema_rejections = asdict(result.schema_rejections)
    cloze_reasons = asdict(result.cloze_ineligibility)
    cloze_eligible_count = sum(
        item.blank_sentence is not None for item in result.items
    )
    return {
        "schema_version": 2,
        "movie_title": result.movie_title,
        "release_year": result.release_year,
        "candidate_limit": result.candidate_limit,
        "prompt": {
            "provider": result.provider_name,
            "model": settings.LLM_MODEL,
            "system_prompt_sha256": hashlib.sha256(
                SYSTEM_PROMPT.encode("utf-8")
            ).hexdigest(),
        },
        "source": source,
        "counts": {
            "provider_returned": result.provider_returned_count,
            "schema_valid": result.schema_valid_count,
            "accepted": result.accepted_count,
            "rejected": result.rejected_count,
            "cloze_eligible": cloze_eligible_count,
            "cloze_ineligible": result.cloze_ineligibility.total,
        },
        "rejections": {
            "duplicate": result.rejections.duplicate,
            "ungrounded": result.rejections.ungrounded,
            "malformed": result.rejections.malformed,
            "over_limit": result.over_limit_count,
            "schema": {
                **schema_rejections,
                "total": result.schema_rejections.total,
            },
        },
        "cloze_ineligibility": {
            **cloze_reasons,
            "total": result.cloze_ineligibility.total,
        },
        "items": _items_payload(result),
    }


def _items_payload(
    result: VocabularyPromptBenchmarkResult,
) -> list[dict[str, object]]:
    return [item.model_dump(mode="json", by_alias=True) for item in result.items]


def _write_json(destination: Path, contents: str) -> None:
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary_name = tempfile.mkstemp(
            prefix=f".{destination.name}.",
            suffix=".tmp",
            dir=destination.parent,
        )
    except OSError as exc:
        raise CommandError(
            f"The output path is not writable: {destination}"
        ) from exc

    temporary_path = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(contents)
        os.replace(temporary_path, destination)
    except OSError as exc:
        temporary_path.unlink(missing_ok=True)
        raise CommandError(
            f"The benchmark JSON could not be written: {destination}"
        ) from exc


class Command(BaseCommand):
    help = (
        "Run the production vocabulary extraction pipeline without writing "
        "to the database."
    )
    missing_args_message = "A movie title is required. Use --movie TITLE."
    requires_system_checks = []
    suppressed_base_arguments = {
        "--version",
        "-v",
        "--verbosity",
        "--settings",
        "--pythonpath",
        "--traceback",
        "--no-color",
        "--force-color",
    }

    def create_parser(self, prog_name, subcommand, **kwargs):
        kwargs.setdefault("formatter_class", BenchmarkHelpFormatter)
        return super().create_parser(prog_name, subcommand, **kwargs)

    def add_arguments(self, parser):
        parser.add_argument(
            "-m",
            "--movie",
            required=True,
            metavar="TITLE",
            help="Movie title supplied to the extraction prompt.",
        )
        parser.add_argument(
            "-y",
            "--year",
            type=int,
            metavar="YEAR",
            help="Release year used to disambiguate the movie and subtitles.",
        )
        parser.add_argument(
            "-l",
            "--limit",
            type=int,
            default=50,
            metavar="COUNT",
            help=(
                "Maximum candidates requested from the provider "
                f"(default: 50; maximum: {MAX_GENERATION_CANDIDATES})."
            ),
        )
        parser.add_argument(
            "-o",
            "--output",
            type=Path,
            metavar="PATH",
            help="Optional JSON output path; an existing file is atomically replaced.",
        )
        parser.add_argument(
            "-f",
            "--source-file",
            type=Path,
            metavar="PATH",
            help="Local .txt or .srt source; bypasses automatic acquisition.",
        )
        parser.add_argument(
            "--words-only",
            action="store_true",
            help="Output only the extracted vocabulary items array.",
        )

    def handle(self, *args, **options):
        movie_title = " ".join(options["movie"].split())
        release_year = options["year"]
        candidate_limit = options["limit"]
        if not movie_title or len(movie_title) > 255:
            raise CommandError("--movie must contain between 1 and 255 characters.")
        max_release_year = current_year()
        if release_year is not None and not (
            1888 <= release_year <= max_release_year
        ):
            raise CommandError(
                f"--year must be between 1888 and {max_release_year}."
            )
        if not 1 <= candidate_limit <= MAX_GENERATION_CANDIDATES:
            raise CommandError(
                f"--limit must be between 1 and {MAX_GENERATION_CANDIDATES}."
            )

        destination = (
            options["output"].expanduser().resolve()
            if options["output"] is not None
            else None
        )
        if destination is not None and destination.exists() and destination.is_dir():
            raise CommandError("--output must name a JSON file, not a directory.")

        prepared_source = None
        source_option = options["source_file"]
        if source_option is not None:
            try:
                source_path = source_option.expanduser().resolve(strict=True)
            except OSError as exc:
                raise CommandError(
                    f"The source file is not available: {source_option}"
                ) from exc
            if destination is not None and destination == source_path:
                raise CommandError("--output cannot overwrite --source-file.")
            try:
                original_source = parse_local_source(
                    source_path,
                    allowed_root=source_path.parent,
                )
                prepared_source = prepare_benchmark_source(
                    original_source,
                    candidate_limit=candidate_limit,
                )
            except (
                SourceIngestionError,
                SubtitleFilterConfigurationError,
                ValueError,
            ) as exc:
                raise CommandError(str(exc)) from exc
            if not prepared_source.text.strip():
                raise CommandError(
                    "The source file contained no locally recognized B1-C2 "
                    "candidate context."
                )
            source_note = (
                f'Source: local {original_source.format.upper()} file "'
                f'{original_source.filename}", pre-filtered in memory.'
            )
            source_details = _source_metadata(
                original=original_source,
                prepared=prepared_source,
                origin="local_file",
                status="used",
                note=source_note,
            )
        else:
            try:
                acquired_source = acquire_automatic_source(
                    title=movie_title,
                    release_year=release_year,
                )
            except SourceAcquisitionError as exc:
                source_note = f"Source: {exc} Using model knowledge."
                source_details = _source_status_metadata(
                    origin="automatic",
                    status="unavailable",
                    provider=_configured_source_provider(),
                    note=source_note,
                )
            else:
                if acquired_source is None:
                    source_note = (
                        "Source: automatic subtitle acquisition is not configured; "
                        "using model knowledge."
                    )
                    source_details = _source_status_metadata(
                        origin="model_knowledge",
                        status="not_configured",
                        note=source_note,
                    )
                else:
                    try:
                        filtered_source = prepare_benchmark_source(
                            acquired_source.document,
                            candidate_limit=candidate_limit,
                        )
                    except (
                        SubtitleFilterConfigurationError,
                        TypeError,
                        ValueError,
                    ):
                        source_note = (
                            "Source: automatically acquired subtitles could not be "
                            "pre-filtered safely; using model knowledge."
                        )
                        source_details = _source_status_metadata(
                            origin="automatic",
                            status="filter_error",
                            provider=acquired_source.provider,
                            source_id=acquired_source.source_id,
                            imdb_id=acquired_source.imdb_id,
                            note=source_note,
                        )
                    else:
                        imdb_reference = f"tt{acquired_source.imdb_id.zfill(7)}"
                        if filtered_source.text.strip():
                            prepared_source = filtered_source
                            source_note = (
                                "Source: found English subtitles on "
                                f"{acquired_source.provider} "
                                f"(IMDb {imdb_reference}, source "
                                f"{acquired_source.source_id}); fetched and "
                                "pre-filtered in memory for this run."
                            )
                            source_status = "used"
                        else:
                            source_note = (
                                "Source: automatically acquired English subtitles "
                                "contained no locally recognized B1-C2 candidate "
                                "context; using model knowledge."
                            )
                            source_status = "filtered_empty"
                        source_details = _source_metadata(
                            original=acquired_source.document,
                            prepared=filtered_source,
                            origin="automatic",
                            status=source_status,
                            provider=acquired_source.provider,
                            source_id=acquired_source.source_id,
                            imdb_id=acquired_source.imdb_id,
                            note=source_note,
                        )

        self.stderr.write(source_note)

        try:
            result = benchmark_vocabulary_prompt(
                movie_title=movie_title,
                release_year=release_year,
                candidate_limit=candidate_limit,
                source=prepared_source,
            )
        except (VocabularyGenerationError, ValueError) as exc:
            raise CommandError(str(exc)) from exc

        payload = (
            _items_payload(result)
            if options["words_only"]
            else _benchmark_payload(result, source=source_details)
        )
        contents = json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
        if destination is None:
            self.stdout.write(contents, ending="")
            return

        _write_json(destination, contents)
        self.stdout.write(
            self.style.SUCCESS(
                f"Benchmark complete: {result.accepted_count} accepted, "
                f"{result.rejected_count} rejected; wrote {destination}"
            )
        )
