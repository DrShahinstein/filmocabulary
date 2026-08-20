from dataclasses import replace

from django.test import TestCase

from quizzes.forms import PracticeSetupForm, QuizAnswerForm
from quizzes.services import (
    CLOZE_MODE,
    DEFINITION_MODE,
    MIXED_MODE,
    generate_question,
)

from .factories import make_movie, make_user, make_vocabulary


class PracticeSetupFormTests(TestCase):
    def test_form_offers_all_three_quiz_modes_with_mixed_initially_selected(self):
        user = make_user("setup-learner")

        form = PracticeSetupForm(user=user)

        self.assertEqual(
            list(form.fields["mode"].choices),
            [
                (DEFINITION_MODE, "Definition only"),
                (CLOZE_MODE, "Fill-in-the-blanks only"),
                (MIXED_MODE, "Mixed"),
            ],
        )
        self.assertEqual(form.fields["mode"].initial, MIXED_MODE)


class QuizAnswerFormTests(TestCase):
    def setUp(self):
        self.user = make_user("form-learner")
        movie = make_movie(self.user)
        for index in range(5):
            make_vocabulary(
                movie,
                word=f"word-{index}",
                definition=f"distinct meaning {index}.",
            )
        self.question = generate_question(user=self.user)

    def test_form_uses_exactly_the_signed_question_options(self):
        form = QuizAnswerForm(question=self.question)

        self.assertEqual(len(form.fields["selected_option"].choices), 5)
        self.assertEqual(
            {choice[0] for choice in form.fields["selected_option"].choices},
            {option.vocabulary_item_id for option in self.question.options},
        )
        self.assertEqual(form.fields["question_token"].initial, self.question.token)

    def test_option_labels_use_parentheses_and_sentence_case(self):
        option = self.question.options[0]
        example_definition = "a perfect and romantic wedding, like in a fairy tale"
        question = replace(
            self.question,
            options=(
                replace(option, definition=example_definition),
                *self.question.options[1:],
            ),
        )

        form = QuizAnswerForm(question=question)
        labels = dict(form.fields["selected_option"].choices)

        self.assertEqual(
            labels[option.vocabulary_item_id],
            f"{option.label}) A perfect and romantic wedding, like in a fairy tale",
        )

    def test_form_rejects_an_option_outside_the_question(self):
        form = QuizAnswerForm(
            {
                "question_token": self.question.token,
                "selected_option": 999999,
            },
            question=self.question,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("selected_option", form.errors)


class ClozeQuizAnswerFormTests(TestCase):
    def setUp(self):
        self.user = make_user("cloze-form-learner")
        movie = make_movie(self.user)
        self.items = [
            make_vocabulary(
                movie,
                word=f"cloze-form-{index}",
                definition=f"Cloze form meaning {index}.",
            )
            for index in range(5)
        ]
        self.question = generate_question(
            user=self.user,
            mode=CLOZE_MODE,
            target=self.items[0],
        )

    def test_form_uses_term_options_from_the_signed_question(self):
        form = QuizAnswerForm(question=self.question)

        self.assertEqual(form.fields["question_token"].initial, self.question.token)
        self.assertEqual(
            form.fields["selected_option"].label,
            "Choose the missing word or phrase",
        )
        self.assertEqual(
            {choice[0] for choice in form.fields["selected_option"].choices},
            {item.pk for item in self.items},
        )
        labels = " ".join(
            choice[1] for choice in form.fields["selected_option"].choices
        )
        for item in self.items:
            self.assertIn(item.word_or_phrase, labels)

    def test_form_rejects_a_missing_selection(self):
        form = QuizAnswerForm(
            {
                "question_token": self.question.token,
            },
            question=self.question,
        )

        self.assertFalse(form.is_valid())
        self.assertIn("selected_option", form.errors)
