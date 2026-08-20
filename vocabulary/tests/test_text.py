from django.test import SimpleTestCase

from vocabulary.text import (
    BlankSentenceError,
    BlankSentenceErrorReason,
    derive_blank_sentence,
)


class BlankSentenceDerivationTests(SimpleTestCase):
    def test_blanks_the_inflected_surface_form(self):
        self.assertEqual(
            derive_blank_sentence(
                "scrutinize",
                "The reporter scrutinized every small detail.",
            ),
            "The reporter ___ every small detail.",
        )

    def test_blanks_both_parts_of_a_separable_phrasal_verb(self):
        self.assertEqual(
            derive_blank_sentence(
                "brush off",
                "She brushed the criticism off immediately.",
            ),
            "She ___ the criticism ___ immediately.",
        )

    def test_classifies_an_ambiguous_inflected_target(self):
        with self.assertRaises(BlankSentenceError) as context:
            derive_blank_sentence(
                "scrutinize",
                "She scrutinized one clue and scrutinized another.",
            )

        self.assertEqual(
            context.exception.reason,
            BlankSentenceErrorReason.AMBIGUOUS_TARGET,
        )

    def test_exact_and_inflected_occurrences_are_still_ambiguous(self):
        with self.assertRaises(BlankSentenceError) as context:
            derive_blank_sentence(
                "scrutinize",
                "They scrutinize one clue after she scrutinized another.",
            )

        self.assertEqual(
            context.exception.reason,
            BlankSentenceErrorReason.AMBIGUOUS_TARGET,
        )

    def test_classifies_a_missing_target(self):
        with self.assertRaises(BlankSentenceError) as context:
            derive_blank_sentence(
                "scrutinize",
                "The reporter reviewed every small detail.",
            )

        self.assertEqual(
            context.exception.reason,
            BlankSentenceErrorReason.MISSING_TARGET,
        )
