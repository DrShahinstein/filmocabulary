import html
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

import srt
from charset_normalizer import from_bytes
from django.conf import settings
from django.utils.html import strip_tags


SourceFormat = Literal["script", "srt"]

_FORMAT_ALIASES: dict[str, SourceFormat] = {
    ".srt": "srt",
    ".txt": "script",
    "srt": "srt",
    "script": "script",
    "text": "script",
    "txt": "script",
}
_SRT_OVERRIDE_TAG = re.compile(r"\{\\[^{}]+\}")


class SourceIngestionError(ValueError):
    """A source-file error whose message is safe to show to an end user."""


@dataclass(frozen=True, slots=True)
class SourceDocument:
    text: str
    format: SourceFormat
    filename: str | None = None
    pre_filtered: bool = False


def _source_max_bytes() -> int:
    value = getattr(settings, "VOCABULARY_SOURCE_MAX_BYTES", 2 * 1024 * 1024)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise SourceIngestionError(
            "Script ingestion is not configured correctly."
        )
    return value


def _normalise_format(value: str) -> SourceFormat:
    source_format = _FORMAT_ALIASES.get(value.strip().casefold())
    if source_format is None:
        raise SourceIngestionError("Upload a .txt script or .srt subtitle file.")
    return source_format


def _validate_size(content: bytes) -> None:
    if len(content) > _source_max_bytes():
        raise SourceIngestionError("The script or subtitle file is too large.")


def _decode_bytes(content: bytes) -> str:
    _validate_size(content)
    if not content:
        raise SourceIngestionError("The script or subtitle file is empty.")

    try:
        return content.decode("utf-8-sig")
    except UnicodeDecodeError:
        match = from_bytes(content).best()
        if match is None:
            raise SourceIngestionError(
                "The script or subtitle file could not be decoded as text."
            )
        return str(match)


def _normalise_lines(value: str) -> str:
    disallowed_controls = sum(
        1 for character in value if ord(character) < 32 and character not in "\t\n\r"
    )
    if "\x00" in value or disallowed_controls:
        raise SourceIngestionError(
            "The script or subtitle file could not be decoded as text."
        )

    output: list[str] = []
    previous_was_blank = False
    for raw_line in value.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = " ".join(raw_line.split())
        if not line:
            if output and not previous_was_blank:
                output.append("")
            previous_was_blank = True
            continue
        output.append(line)
        previous_was_blank = False
    return "\n".join(output).strip()


def _parse_script(value: str) -> str:
    return _normalise_lines(value)


def _parse_srt(value: str) -> str:
    try:
        subtitles = list(srt.parse(value, ignore_errors=False))
    except (srt.SRTParseError, ValueError) as exc:
        raise SourceIngestionError(
            "The subtitle file is not valid SubRip (.srt) content."
        ) from exc

    dialogue: list[str] = []
    for subtitle in subtitles:
        content = _SRT_OVERRIDE_TAG.sub("", subtitle.content)
        content = html.unescape(strip_tags(content))
        content = " ".join(content.split())
        if content and (not dialogue or dialogue[-1] != content):
            dialogue.append(content)

    return "\n".join(dialogue).strip()


def parse_source_text(
    content: str | bytes,
    *,
    source_format: str,
    filename: str | None = None,
) -> SourceDocument:
    """Parse trusted local or already-fetched source content for LLM ingestion."""
    parsed_format = _normalise_format(source_format)
    if isinstance(content, bytes):
        decoded = _decode_bytes(content)
    elif isinstance(content, str):
        encoded = content.encode("utf-8")
        _validate_size(encoded)
        decoded = content
    else:
        raise TypeError("content must be text or bytes")

    parsed_text = (
        _parse_srt(decoded) if parsed_format == "srt" else _parse_script(decoded)
    )
    if not parsed_text:
        raise SourceIngestionError(
            "The script or subtitle file did not contain usable text."
        )

    safe_filename = Path(filename).name if filename else None
    return SourceDocument(
        text=parsed_text,
        format=parsed_format,
        filename=safe_filename,
    )


def parse_uploaded_source(uploaded_file: Any) -> SourceDocument:
    filename = Path(str(getattr(uploaded_file, "name", ""))).name
    source_format = _normalise_format(Path(filename).suffix)
    max_bytes = _source_max_bytes()
    declared_size = getattr(uploaded_file, "size", None)
    if isinstance(declared_size, int) and declared_size > max_bytes:
        raise SourceIngestionError("The script or subtitle file is too large.")
    content = bytearray()

    try:
        for chunk in uploaded_file.chunks():
            content.extend(chunk)
            if len(content) > max_bytes:
                raise SourceIngestionError(
                    "The script or subtitle file is too large."
                )
    finally:
        try:
            uploaded_file.seek(0)
        except (AttributeError, OSError):
            pass

    return parse_source_text(
        bytes(content),
        source_format=source_format,
        filename=filename,
    )


def parse_local_source(
    path: str | Path,
    *,
    allowed_root: str | Path,
) -> SourceDocument:
    """Read a server-side source file confined to an explicitly trusted root."""
    try:
        root = Path(allowed_root).resolve(strict=True)
        source_path = Path(path).resolve(strict=True)
        source_path.relative_to(root)
    except (OSError, ValueError) as exc:
        raise SourceIngestionError(
            "The local script or subtitle path is not available."
        ) from exc

    if not root.is_dir() or not source_path.is_file():
        raise SourceIngestionError(
            "The local script or subtitle path is not available."
        )
    try:
        if source_path.stat().st_size > _source_max_bytes():
            raise SourceIngestionError("The script or subtitle file is too large.")
        content = source_path.read_bytes()
    except SourceIngestionError:
        raise
    except OSError as exc:
        raise SourceIngestionError(
            "The local script or subtitle file could not be read."
        ) from exc

    return parse_source_text(
        content,
        source_format=source_path.suffix,
        filename=source_path.name,
    )
