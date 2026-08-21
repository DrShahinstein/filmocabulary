import hashlib
import json
import os
import tempfile
from dataclasses import asdict
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from vocabulary.constants import MAX_GENERATION_CANDIDATES
from vocabulary.ingestion import SourceIngestionError, parse_local_source
from vocabulary.providers import SYSTEM_PROMPT
from vocabulary.services import (
    VocabularyGenerationError,
    VocabularyPromptBenchmarkResult,
    benchmark_vocabulary_prompt,
    prepare_benchmark_source,
)


def _source_metadata(*, original, prepared) -> dict[str, object]:
    return {
        "filename": original.filename,
        "format": original.format,
        "input_characters": len(original.text),
        "prompt_characters": len(prepared.text),
        "prompt_sha256": hashlib.sha256(prepared.text.encode("utf-8")).hexdigest(),
        "pre_filtered": prepared.pre_filtered,
    }


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
        "schema_version": 1,
        "movie_title": result.movie_title,
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
        "items": [
            item.model_dump(mode="json", by_alias=True) for item in result.items
        ],
    }


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
    help = "Benchmark the configured vocabulary extraction prompt without database writes."

    def add_arguments(self, parser):
        parser.add_argument(
            "-m",
            "--movie",
            required=True,
            help="Movie title supplied to the extraction prompt.",
        )
        parser.add_argument(
            "-l",
            "--limit",
            type=int,
            default=50,
            help=(
                "Maximum candidates requested from the provider "
                f"(default: 50; maximum: {MAX_GENERATION_CANDIDATES})."
            ),
        )
        parser.add_argument(
            "-o",
            "--output",
            type=Path,
            help="Optional JSON output path; an existing file is atomically replaced.",
        )
        parser.add_argument(
            "-f",
            "--source-file",
            type=Path,
            help="Optional local .txt transcript or .srt subtitle file.",
        )

    def handle(self, *args, **options):
        movie_title = " ".join(options["movie"].split())
        candidate_limit = options["limit"]
        if not movie_title or len(movie_title) > 255:
            raise CommandError("--movie must contain between 1 and 255 characters.")
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
        source_details = None
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
            except (SourceIngestionError, ValueError) as exc:
                raise CommandError(str(exc)) from exc
            if not prepared_source.text.strip():
                raise CommandError(
                    "The source file contained no locally recognized B1-C2 "
                    "candidate context."
                )
            source_details = _source_metadata(
                original=original_source,
                prepared=prepared_source,
            )

        try:
            result = benchmark_vocabulary_prompt(
                movie_title=movie_title,
                candidate_limit=candidate_limit,
                source=prepared_source,
            )
        except (VocabularyGenerationError, ValueError) as exc:
            raise CommandError(str(exc)) from exc

        payload = _benchmark_payload(result, source=source_details)
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
