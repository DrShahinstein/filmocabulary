from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from quizzes.models import UserWordStatus
from quizzes.services import generate_question

from .factories import make_movie, make_user, make_vocabulary


class QuizViewTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = make_user("view-learner")
        self.other_user = make_user("view-other")
        self.movie = make_movie(self.user)
        self.items = [
            make_vocabulary(
                self.movie,
                word=f"word-{index}",
                definition=f"Meaning {index}.",
            )
            for index in range(6)
        ]
        other_movie = make_movie(self.other_user, "Heat", 1995)
        self.outsider = make_vocabulary(other_movie, word="outsider")
        self.client.force_login(self.user)

    def test_progress_dashboard_requires_login(self):
        self.client.logout()
        url = reverse("quizzes:dashboard")

        response = self.client.get(url)

        self.assertRedirects(response, f"{reverse('login')}?next={url}")

    def test_dashboard_summarizes_mastered_learning_and_new_words(self):
        UserWordStatus.objects.create(
            user=self.user,
            vocabulary_item=self.items[0],
            status=UserWordStatus.Status.MASTERED,
        )
        UserWordStatus.objects.create(
            user=self.user,
            vocabulary_item=self.items[1],
            status=UserWordStatus.Status.LEARNING,
            wrong_count=1,
        )

        response = self.client.get(reverse("quizzes:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.context["progress"],
            {"total": 6, "new": 4, "learning": 1, "mastered": 1},
        )
        self.assertContains(response, "Your progress")
        self.assertNotContains(response, "Quiz history")

    def test_learning_pool_lists_only_current_users_learning_words(self):
        learning = UserWordStatus.objects.create(
            user=self.user,
            vocabulary_item=self.items[0],
            status=UserWordStatus.Status.LEARNING,
            wrong_count=2,
        )
        UserWordStatus.objects.create(
            user=self.user,
            vocabulary_item=self.items[1],
            status=UserWordStatus.Status.MASTERED,
        )
        UserWordStatus.objects.create(
            user=self.other_user,
            vocabulary_item=self.outsider,
            status=UserWordStatus.Status.LEARNING,
        )

        response = self.client.get(reverse("quizzes:learning_pool"))

        self.assertQuerySetEqual(response.context["word_statuses"], [learning])
        self.assertContains(response, self.items[0].word_or_phrase)
        self.assertContains(response, self.items[0].definition_en)
        self.assertContains(response, self.items[0].example_sentence)
        self.assertContains(response, self.movie.title)
        self.assertNotContains(response, self.items[1].word_or_phrase)
        self.assertNotContains(response, self.outsider.word_or_phrase)

    def test_empty_learning_pool_has_helpful_state(self):
        response = self.client.get(reverse("quizzes:learning_pool"))

        self.assertContains(response, "Your Learning Pool is clear")

    def test_question_view_renders_five_radio_options(self):
        response = self.client.get(
            reverse("quizzes:question", args=["collection"])
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "quizzes/practice.html")
        self.assertContains(response, 'type="radio"', count=5)
        self.assertNotContains(response, self.outsider.definition_en)

    def test_htmx_question_returns_only_question_partial(self):
        response = self.client.get(
            reverse("quizzes:question", args=["collection"]),
            HTTP_HX_REQUEST="true",
        )

        self.assertTemplateUsed(response, "partials/mcq_question.html")
        self.assertTemplateNotUsed(response, "quizzes/practice.html")

    def test_learning_question_targets_only_learning_word(self):
        learning = self.items[3]
        UserWordStatus.objects.create(
            user=self.user,
            vocabulary_item=learning,
            status=UserWordStatus.Status.LEARNING,
            wrong_count=1,
        )

        response = self.client.get(reverse("quizzes:question", args=["learning"]))

        self.assertEqual(response.context["question"].target, learning)

    def test_correct_post_updates_status_and_returns_feedback(self):
        question = generate_question(user=self.user, target=self.items[0])

        response = self.client.post(
            reverse("quizzes:answer", args=["collection"]),
            {
                "question_token": question.token,
                "selected_option": self.items[0].pk,
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "partials/mcq_feedback.html")
        self.assertContains(response, "Correct · Mastered")
        self.assertEqual(
            UserWordStatus.objects.get(
                user=self.user,
                vocabulary_item=self.items[0],
            ).status,
            UserWordStatus.Status.MASTERED,
        )

    def test_wrong_post_updates_learning_pool_and_returns_feedback(self):
        question = generate_question(user=self.user, target=self.items[0])
        wrong_id = next(
            option.vocabulary_item_id
            for option in question.options
            if option.vocabulary_item_id != self.items[0].pk
        )

        response = self.client.post(
            reverse("quizzes:answer", args=["collection"]),
            {"question_token": question.token, "selected_option": wrong_id},
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(response, "Added to Learning Pool")
        status = UserWordStatus.objects.get(
            user=self.user,
            vocabulary_item=self.items[0],
        )
        self.assertEqual(status.status, UserWordStatus.Status.LEARNING)
        self.assertEqual(status.wrong_count, 1)

    def test_answer_rejects_option_not_present_in_signed_question(self):
        question = generate_question(user=self.user, target=self.items[0])

        response = self.client.post(
            reverse("quizzes:answer", args=["collection"]),
            {"question_token": question.token, "selected_option": self.outsider.pk},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 422)
        self.assertFalse(UserWordStatus.objects.filter(user=self.user).exists())

    def test_answer_rejects_question_from_different_url_pool(self):
        question = generate_question(user=self.user, target=self.items[0])

        response = self.client.post(
            reverse("quizzes:answer", args=["learning"]),
            {
                "question_token": question.token,
                "selected_option": self.items[0].pk,
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(UserWordStatus.objects.filter(user=self.user).exists())

    def test_answer_is_post_only(self):
        response = self.client.get(
            reverse("quizzes:answer", args=["collection"])
        )

        self.assertEqual(response.status_code, 405)

    def test_invalid_pool_is_handled_gracefully(self):
        response = self.client.get(reverse("quizzes:question", args=["invalid"]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choose a valid practice pool")

    def test_question_handles_library_with_too_few_words(self):
        small_user = make_user("view-small")
        movie = make_movie(small_user, "Primer", 2004)
        make_vocabulary(movie)
        self.client.force_login(small_user)

        response = self.client.get(
            reverse("quizzes:question", args=["collection"])
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "at least five vocabulary entries")
