from django.test import TestCase

from quizzes.forms import QuizAnswerForm
from quizzes.services import generate_question

from .factories import make_movie, make_user, make_vocabulary


class QuizAnswerFormTests(TestCase):
    def setUp(self):
        self.user = make_user("form-learner")
        movie = make_movie(self.user)
        for index in range(5):
            make_vocabulary(
                movie,
                word=f"word-{index}",
                definition=f"Distinct meaning {index}.",
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
