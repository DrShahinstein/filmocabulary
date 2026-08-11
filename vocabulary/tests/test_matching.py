from django.test import SimpleTestCase

from vocabulary.matching import source_contains_term


class SourceTermMatchingTests(SimpleTestCase):
    def test_matches_inflected_verb_to_lemma(self):
        self.assertTrue(
            source_contains_term(
                "The reporter scrutinized every detail.",
                "scrutinize",
            )
        )

    def test_matches_plural_to_singular(self):
        self.assertTrue(source_contains_term("Pack the utensils.", "utensil"))

    def test_matches_inflections_across_a_phrase(self):
        self.assertTrue(
            source_contains_term(
                "The doctor ruled out surgeries.",
                "rule out surgery",
            )
        )

    def test_matches_case_hyphen_and_unicode_apostrophe_variations(self):
        self.assertTrue(source_contains_term("A CUTTING-EDGE method.", "cutting edge"))
        self.assertTrue(source_contains_term("They wouldn\u2019t back down.", "wouldn't"))

    def test_matches_expanded_contractions(self):
        self.assertTrue(source_contains_term("They won't back down.", "will not"))
        self.assertTrue(source_contains_term("We've ruled it out.", "we have"))

    def test_matches_possessive_and_irregular_forms(self):
        self.assertTrue(source_contains_term("The scientist's warning was heard.", "scientist"))
        self.assertTrue(source_contains_term("The proposal was better.", "good"))
        self.assertTrue(source_contains_term("Several people were waiting.", "person"))

    def test_matches_comparative_and_adverbial_forms(self):
        self.assertTrue(source_contains_term("A subtler approach was needed.", "subtle"))
        self.assertTrue(source_contains_term("They worked systematically.", "systematic"))

    def test_matches_separable_phrasal_verb(self):
        self.assertTrue(source_contains_term("She backed her colleague up.", "back up"))

    def test_does_not_match_inside_a_larger_word(self):
        self.assertFalse(source_contains_term("They overscrutinized it.", "scrutinize"))

    def test_does_not_match_unrelated_term(self):
        self.assertFalse(source_contains_term("They examined it.", "scrutinize"))
