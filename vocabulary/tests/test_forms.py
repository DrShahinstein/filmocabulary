from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import SimpleTestCase

from vocabulary.forms import GenerateVocabularyForm, VocabularyGenerationForm


class VocabularyGenerationFormTests(SimpleTestCase):
    def test_movie_fields_use_matrix_placeholders(self):
        form = VocabularyGenerationForm()

        self.assertEqual(
            form.fields["title"].widget.attrs["placeholder"],
            "The Matrix",
        )
        self.assertEqual(
            form.fields["release_year"].widget.attrs["placeholder"],
            "1999",
        )

    def test_compatibility_alias_points_to_public_form(self):
        self.assertIs(GenerateVocabularyForm, VocabularyGenerationForm)

    def test_title_is_normalised_and_year_is_optional(self):
        form = VocabularyGenerationForm(
            {"title": "  The   Conversation ", "release_year": "", "item_count": 8}
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["title"], "The Conversation")
        self.assertIsNone(form.cleaned_data["release_year"])
        self.assertIsNone(form.source_document)

    def test_accepts_item_count_at_upper_boundary(self):
        form = VocabularyGenerationForm(
            {"title": "Arrival", "release_year": 2016, "item_count": 100}
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data["item_count"], 100)

    def test_rejects_item_count_above_upper_boundary(self):
        form = VocabularyGenerationForm(
            {"title": "Arrival", "release_year": 2016, "item_count": 101}
        )

        self.assertFalse(form.is_valid())
        self.assertIn("item_count", form.errors)

    def test_accepts_and_parses_plain_script_upload(self):
        upload = SimpleUploadedFile(
            "arrival.txt",
            b"INT. CLASSROOM - DAY\n\nThe linguist remains composed.",
            content_type="text/plain",
        )
        form = VocabularyGenerationForm(
            {"title": "Arrival", "release_year": 2016, "item_count": 20},
            {"source_file": upload},
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.source_document.format, "script")
        self.assertEqual(
            form.source_document.text,
            "INT. CLASSROOM - DAY\n\nThe linguist remains composed.",
        )

    def test_accepts_and_parses_srt_upload(self):
        upload = SimpleUploadedFile(
            "arrival.srt",
            b"1\n00:00:01,000 --> 00:00:03,000\nRemain composed.\n",
            content_type="application/x-subrip",
        )
        form = VocabularyGenerationForm(
            {"title": "Arrival", "release_year": 2016, "item_count": 20},
            {"source_file": upload},
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.source_document.format, "srt")
        self.assertEqual(form.source_document.text, "Remain composed.")

    def test_reports_invalid_upload_as_source_file_error(self):
        upload = SimpleUploadedFile(
            "arrival.pdf",
            b"not a supported source",
            content_type="application/pdf",
        )
        form = VocabularyGenerationForm(
            {"title": "Arrival", "release_year": 2016, "item_count": 20},
            {"source_file": upload},
        )

        self.assertFalse(form.is_valid())
        self.assertIn("source_file", form.errors)
