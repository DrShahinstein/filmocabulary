import json
from importlib.resources import files

from django.test import SimpleTestCase

from vocabulary.ingestion import SourceDocument
from vocabulary.subtitle_filter import (
    DEFAULT_MAX_CHARACTERS,
    DEFAULT_MAX_WORDS,
    FILTER_VERSION,
    filter_subtitle,
    filter_subtitle_document,
    filter_subtitle_text,
)


class SubtitleVocabularyDatasetTests(SimpleTestCase):
    def test_bundled_dataset_has_expected_provenance_and_coverage(self):
        resource = files("vocabulary").joinpath("data", "cefr_words.json")
        with resource.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        self.assertEqual(payload["_meta"]["filter_version"], FILTER_VERSION)
        self.assertGreater(payload["_meta"]["entry_counts"]["B1"], 2_000)
        self.assertGreater(payload["_meta"]["entry_counts"]["B2"], 2_000)
        self.assertGreater(payload["_meta"]["entry_counts"]["C1"], 800)
        self.assertGreater(payload["_meta"]["entry_counts"]["C2"], 800)
        self.assertGreater(payload["_meta"]["entry_counts"]["multiword"], 50)
        self.assertEqual(payload["entries"]["scrutinize"], "C1")
        self.assertEqual(payload["entries"]["mull over"], "C2")
        self.assertEqual(payload["entries"]["abandon"], "B1")

    def test_common_polysemes_with_lower_levels_are_not_candidates(self):
        result = filter_subtitle("I will wind the clock before bed.")

        self.assertEqual(result.text, "")
        self.assertEqual(result.matched_terms, ())


class SubtitleFilteringTests(SimpleTestCase):
    def test_drops_elementary_units_and_keeps_advanced_unit_intact(self):
        source = (
            "Hello, I am here.\n"
            "We must scrutinize every detail before deciding.\n"
            "The cat is on the chair."
        )

        result = filter_subtitle(source)

        self.assertEqual(
            result.text,
            "We must scrutinize every detail before deciding.",
        )
        self.assertIn("scrutinize", result.matched_terms)
        self.assertEqual(result.source_unit_count, 3)
        self.assertEqual(result.kept_unit_count, 1)

    def test_keeps_b1_units_but_drops_a1_a2_chatter(self):
        source = (
            "Hello, I am coming.\n"
            "They had to abandon the damaged vehicle.\n"
            "Nice to meet you."
        )

        result = filter_subtitle(source)

        self.assertEqual(
            result.text,
            "They had to abandon the damaged vehicle.",
        )
        self.assertIn("abandon", result.matched_terms)
        self.assertNotIn("hello", result.matched_terms)
        self.assertNotIn("coming", result.matched_terms)
        self.assertNotIn("nice", result.matched_terms)

    def test_groups_fragmented_cues_into_a_complete_utterance(self):
        source = (
            "We need to\n"
            "scrutinize every clue\n"
            "before dawn.\n"
            "I like tea."
        )

        filtered = filter_subtitle_text(source)

        self.assertEqual(
            filtered,
            "We need to\nscrutinize every clue\nbefore dawn.",
        )

    def test_matches_inflected_advanced_word(self):
        result = filter_subtitle(
            "They scrutinized the letter twice.\nI went home."
        )

        self.assertEqual(result.text, "They scrutinized the letter twice.")
        self.assertIn("scrutinize", result.matched_terms)

    def test_matches_hyphenated_multiword_expression(self):
        result = filter_subtitle(
            "The witness looked worn-out after the interview.\nI am fine."
        )

        self.assertEqual(
            result.text,
            "The witness looked worn-out after the interview.",
        )
        self.assertIn("worn out", result.matched_terms)

    def test_matches_separated_phrasal_verb(self):
        result = filter_subtitle(
            "She mulled the strange proposal over before dawn.\n"
            "Then she went home."
        )

        self.assertEqual(
            result.text,
            "She mulled the strange proposal over before dawn.",
        )
        self.assertIn("mull over", result.matched_terms)

    def test_prefers_higher_level_candidate_under_tight_budget(self):
        b2_sentence = "The abandoned office was empty."
        c2_sentence = "Her meticulous notes covered every detail."

        result = filter_subtitle(
            f"{b2_sentence}\n{c2_sentence}",
            max_words=20,
            max_characters=max(len(b2_sentence), len(c2_sentence)),
        )

        self.assertEqual(result.text, c2_sentence)
        self.assertTrue(result.truncated)
        self.assertIn("meticulous", result.matched_terms)

    def test_b1_is_ranked_below_higher_level_candidates(self):
        b1_sentence = "They had to abandon the vehicle."
        c1_sentence = "We must scrutinize every detail."

        result = filter_subtitle(
            f"{b1_sentence}\n{c1_sentence}",
            max_words=20,
            max_characters=max(len(b1_sentence), len(c1_sentence)),
        )

        self.assertEqual(result.text, c1_sentence)
        self.assertTrue(result.truncated)
        self.assertIn("scrutinize", result.matched_terms)

    def test_default_budget_never_splits_an_utterance(self):
        def letters(number):
            output = ""
            while True:
                number, remainder = divmod(number, 26)
                output = chr(97 + remainder) + output
                if number == 0:
                    return output
                number -= 1

        lines = [
            (
                f"Marker{letters(index)} asks us to scrutinize every unusual "
                "detail in this deliberately extended subtitle sentence."
            )
            for index in range(200)
        ]

        result = filter_subtitle("\n".join(lines))

        self.assertLessEqual(result.word_count, DEFAULT_MAX_WORDS)
        self.assertLessEqual(len(result.text), DEFAULT_MAX_CHARACTERS)
        self.assertTrue(result.truncated)
        self.assertTrue(all(line in lines for line in result.text.splitlines()))

    def test_deduplicates_repeated_advanced_utterances(self):
        sentence = "We must scrutinize every detail."

        result = filter_subtitle(f"{sentence}\n{sentence}\n{sentence}")

        self.assertEqual(result.text, sentence)
        self.assertEqual(result.kept_unit_count, 1)

    def test_rejects_invalid_budget(self):
        with self.assertRaisesRegex(ValueError, "max_words"):
            filter_subtitle("We must scrutinize it.", max_words=0)
        with self.assertRaisesRegex(ValueError, "max_characters"):
            filter_subtitle("We must scrutinize it.", max_characters=True)


class SubtitleDocumentFilteringTests(SimpleTestCase):
    def test_returns_document_with_filtered_text_and_original_metadata(self):
        document = SourceDocument(
            text="I like tea.\nWe must scrutinize the evidence.",
            format="srt",
            filename="movie.en.srt",
        )

        filtered = filter_subtitle_document(document)

        self.assertEqual(filtered.text, "We must scrutinize the evidence.")
        self.assertEqual(filtered.format, "srt")
        self.assertEqual(filtered.filename, "movie.en.srt")
        self.assertIsNot(filtered, document)

    def test_allows_empty_filtered_document_for_prompt_only_fallback(self):
        document = SourceDocument(
            text="I like tea.",
            format="srt",
            filename="movie.en.srt",
        )

        filtered = filter_subtitle_document(document)

        self.assertEqual(filtered.text, "")

    def test_rejects_non_document_input(self):
        with self.assertRaisesRegex(TypeError, "SourceDocument"):
            filter_subtitle_document("not a document")
