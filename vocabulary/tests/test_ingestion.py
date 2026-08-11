from pathlib import Path
from tempfile import TemporaryDirectory

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase, override_settings

from vocabulary.ingestion import (
    SourceIngestionError,
    parse_local_source,
    parse_source_text,
    parse_uploaded_source,
)


class SourceTextParsingTests(SimpleTestCase):
    def test_normalises_plain_script_text(self):
        document = parse_source_text(
            b"\xef\xbb\xbf  INT. OFFICE - NIGHT  \r\n\r\n\r\n"
            b"The   reporter studies the file.\r\n",
            source_format="script",
            filename="../Zodiac.txt",
        )

        self.assertEqual(document.format, "script")
        self.assertEqual(document.filename, "Zodiac.txt")
        self.assertEqual(
            document.text,
            "INT. OFFICE - NIGHT\n\nThe reporter studies the file.",
        )

    def test_parses_and_normalises_srt_dialogue(self):
        document = parse_source_text(
            """1
00:00:01,000 --> 00:00:03,000
<i>We need to scrutinize</i>
this clue &amp; every detail.

2
00:00:03,500 --> 00:00:05,000
{\\an8}No loose ends.

3
00:00:05,500 --> 00:00:07,000
No loose ends.
""",
            source_format="srt",
            filename="Zodiac.srt",
        )

        self.assertEqual(document.format, "srt")
        self.assertEqual(document.filename, "Zodiac.srt")
        self.assertEqual(
            document.text,
            "We need to scrutinize this clue & every detail.\nNo loose ends.",
        )

    def test_rejects_malformed_srt(self):
        with self.assertRaisesRegex(SourceIngestionError, "not valid SubRip"):
            parse_source_text(
                "This has no subtitle indexes or timestamps.",
                source_format="srt",
            )

    def test_rejects_binary_content(self):
        with self.assertRaisesRegex(SourceIngestionError, "could not be decoded"):
            parse_source_text(
                b"Dialogue\x00\x01\x02",
                source_format="script",
            )


class UploadedSourceParsingTests(SimpleTestCase):
    def test_accepts_plain_script_upload(self):
        upload = SimpleUploadedFile(
            "conversation.txt",
            b"INT. ROOM - DAY\n\nThey pore over the notes.",
            content_type="text/plain",
        )

        document = parse_uploaded_source(upload)

        self.assertEqual(document.format, "script")
        self.assertEqual(
            document.text,
            "INT. ROOM - DAY\n\nThey pore over the notes.",
        )

    def test_accepts_srt_upload(self):
        upload = SimpleUploadedFile(
            "conversation.srt",
            b"1\n00:00:01,000 --> 00:00:02,000\nStay vigilant.\n",
            content_type="application/x-subrip",
        )

        document = parse_uploaded_source(upload)

        self.assertEqual(document.format, "srt")
        self.assertEqual(document.text, "Stay vigilant.")

    def test_rejects_unsupported_extension(self):
        upload = SimpleUploadedFile(
            "conversation.pdf",
            b"Not really a PDF.",
            content_type="application/pdf",
        )

        with self.assertRaisesRegex(SourceIngestionError, r"\.txt script or \.srt"):
            parse_uploaded_source(upload)

    @override_settings(VOCABULARY_SOURCE_MAX_BYTES=16)
    def test_rejects_oversized_upload(self):
        upload = SimpleUploadedFile(
            "conversation.txt",
            b"A" * 17,
            content_type="text/plain",
        )

        with self.assertRaisesRegex(SourceIngestionError, "too large"):
            parse_uploaded_source(upload)

    def test_rejects_binary_upload(self):
        upload = SimpleUploadedFile(
            "conversation.txt",
            b"Valid-looking prefix\x00\x01\x02",
            content_type="text/plain",
        )

        with self.assertRaisesRegex(SourceIngestionError, "could not be decoded"):
            parse_uploaded_source(upload)


class LocalSourceParsingTests(SimpleTestCase):
    def test_reads_a_local_source_below_the_allowed_root(self):
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            source_path = root / "zodiac.srt"
            source_path.write_text(
                "1\n00:00:01,000 --> 00:00:02,000\nStay vigilant.\n",
                encoding="utf-8",
            )

            document = parse_local_source(source_path, allowed_root=root)

        self.assertEqual(document.format, "srt")
        self.assertEqual(document.filename, "zodiac.srt")
        self.assertEqual(document.text, "Stay vigilant.")

    def test_rejects_a_local_path_outside_the_allowed_root(self):
        with TemporaryDirectory() as root_directory, TemporaryDirectory() as other:
            outside_path = Path(other) / "outside.txt"
            outside_path.write_text("Do not read this.", encoding="utf-8")

            with self.assertRaisesRegex(SourceIngestionError, "not available"):
                parse_local_source(outside_path, allowed_root=root_directory)
