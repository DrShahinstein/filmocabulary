from urllib.parse import parse_qs, urlparse

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from quizzes.models import UserWordStatus
from quizzes.services import (
    CLOZE_MODE,
    DEFINITION_MODE,
    TARGETED_POOL,
    generate_question,
    sign_targeted_scope,
    targeted_scope_from_token,
)
from vocabulary.querysets import VocabularyFilterSpec

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
        self.assertContains(response, "Practice by movie")
        self.assertContains(response, "Saved words (0)")
        self.assertNotContains(response, "Quiz history")
        self.assertContains(
            response,
            f'{reverse("words:index")}?status=mastered',
        )
        self.assertContains(
            response,
            f'{reverse("words:index")}?status=learning',
        )
        self.assertContains(response, f'{reverse("words:index")}?status=new')
        self.assertContains(response, f'{reverse("words:index")}?status=saved')

    def test_dashboard_offers_definition_cloze_and_mixed_practice_modes(self):
        response = self.client.get(reverse("quizzes:dashboard"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'name="mode"', count=3)
        self.assertContains(response, 'value="definition"')
        self.assertContains(response, 'value="cloze"')
        self.assertContains(response, 'value="mixed"')
        self.assertContains(response, "Definition only")
        self.assertContains(response, "Fill-in-the-blanks only")
        self.assertContains(response, "Mixed")

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

    def test_cloze_question_shows_blank_prompt_without_revealing_answer(self):
        response = self.client.get(
            reverse("quizzes:question", args=["collection"]),
            {"mode": CLOZE_MODE},
        )

        self.assertEqual(response.status_code, 200)
        question = response.context["question"]
        self.assertEqual(question.kind, CLOZE_MODE)
        self.assertContains(response, question.target.blank_sentence)
        self.assertContains(response, 'name="answer"')
        self.assertNotContains(response, question.target.word_or_phrase)
        self.assertNotContains(response, question.target.definition_en)
        self.assertNotContains(response, 'name="selected_option"')

    def test_htmx_question_returns_only_question_partial(self):
        response = self.client.get(
            reverse("quizzes:question", args=["collection"]),
            HTTP_HX_REQUEST="true",
        )

        self.assertTemplateUsed(response, "partials/mcq_question.html")
        self.assertTemplateNotUsed(response, "quizzes/practice.html")

    def test_question_card_includes_skip_and_bookmark_controls(self):
        response = self.client.get(
            reverse("quizzes:question", args=["collection"])
        )

        self.assertContains(response, "Skip")
        self.assertContains(response, reverse("quizzes:skip", args=["collection"]))
        self.assertContains(response, "Save this word")

    def test_question_filter_scopes_target_and_options_to_selected_movies(self):
        selected_movie = make_movie(self.user, "The Matrix", 1999)
        selected_items = [
            make_vocabulary(
                selected_movie,
                word=f"selected-{index}",
                definition=f"Selected meaning {index}.",
            )
            for index in range(5)
        ]

        response = self.client.get(
            reverse("quizzes:question", args=["collection"]),
            {"movies": [selected_movie.pk]},
        )

        question = response.context["question"]
        self.assertEqual(question.target.movie, selected_movie)
        self.assertEqual(question.movie_ids, (selected_movie.pk,))
        self.assertEqual(
            {option.vocabulary_item_id for option in question.options},
            {item.pk for item in selected_items},
        )

    def test_question_filter_rejects_another_users_movie(self):
        response = self.client.get(
            reverse("quizzes:question", args=["collection"]),
            {"movies": [self.outsider.movie_id]},
        )

        self.assertEqual(response.status_code, 400)
        self.assertContains(
            response,
            "Choose valid movies from your library.",
            status_code=400,
        )

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

    def test_blank_cloze_answer_returns_validation_feedback_without_progress(self):
        question = generate_question(
            user=self.user,
            mode=CLOZE_MODE,
            target=self.items[0],
        )

        response = self.client.post(
            reverse("quizzes:answer", args=["collection"]),
            {
                "question_token": question.token,
                "answer": "   ",
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 422)
        self.assertTemplateUsed(response, "partials/mcq_question.html")
        self.assertContains(response, "This field is required.", status_code=422)
        self.assertContains(response, question.target.blank_sentence, status_code=422)
        self.assertFalse(UserWordStatus.objects.filter(user=self.user).exists())

    def test_correct_tracked_cloze_answer_marks_word_mastered(self):
        question = generate_question(
            user=self.user,
            mode=CLOZE_MODE,
            target=self.items[0],
        )

        response = self.client.post(
            reverse("quizzes:answer", args=["collection"]),
            {
                "question_token": question.token,
                "answer": question.target.word_or_phrase,
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Correct · Mastered")
        self.assertNotContains(response, "Progress unchanged")
        status = UserWordStatus.objects.get(
            user=self.user,
            vocabulary_item=question.target,
        )
        self.assertEqual(status.status, UserWordStatus.Status.MASTERED)
        self.assertEqual(status.correct_count, 1)
        self.assertEqual(status.wrong_count, 0)

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

    def test_skip_returns_another_question_without_changing_status(self):
        question = generate_question(user=self.user, target=self.items[0])

        response = self.client.post(
            reverse("quizzes:skip", args=["collection"]),
            {"question_token": question.token},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, "partials/mcq_question.html")
        self.assertNotEqual(response.context["question"].target, question.target)
        self.assertFalse(UserWordStatus.objects.filter(user=self.user).exists())

    def test_targeted_skip_and_next_url_preserve_signed_scope_and_mode(self):
        filter_spec = VocabularyFilterSpec(movie_id=self.movie.pk)
        scope_token = sign_targeted_scope(
            user=self.user,
            filter_spec=filter_spec,
        )
        launch_response = self.client.get(
            reverse("quizzes:question", args=[TARGETED_POOL]),
            {"mode": CLOZE_MODE, "scope": scope_token},
            HTTP_HX_REQUEST="true",
        )
        question = launch_response.context["question"]

        skip_response = self.client.post(
            reverse("quizzes:skip", args=[TARGETED_POOL]),
            {"question_token": question.token},
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(skip_response.status_code, 200)
        skipped_question = skip_response.context["question"]
        self.assertNotEqual(skipped_question.target, question.target)
        self.assertEqual(skipped_question.pool, TARGETED_POOL)
        self.assertEqual(skipped_question.mode, CLOZE_MODE)
        self.assertEqual(skipped_question.filter_spec, filter_spec)
        skip_next_query = parse_qs(
            urlparse(skip_response.context["next_question_url"]).query
        )
        self.assertEqual(skip_next_query["mode"], [CLOZE_MODE])
        self.assertEqual(
            targeted_scope_from_token(
                user=self.user,
                token=skip_next_query["scope"][0],
            ),
            filter_spec,
        )

        answer_response = self.client.post(
            reverse("quizzes:answer", args=[TARGETED_POOL]),
            {
                "question_token": skipped_question.token,
                "answer": skipped_question.target.word_or_phrase,
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(answer_response.status_code, 200)
        answer_next_query = parse_qs(
            urlparse(answer_response.context["next_question_url"]).query
        )
        self.assertEqual(answer_next_query["mode"], [CLOZE_MODE])
        self.assertEqual(
            targeted_scope_from_token(
                user=self.user,
                token=answer_next_query["scope"][0],
            ),
            filter_spec,
        )
        self.assertFalse(UserWordStatus.objects.filter(user=self.user).exists())

    def test_bookmark_toggle_preserves_learning_state(self):
        status = UserWordStatus.objects.create(
            user=self.user,
            vocabulary_item=self.items[0],
            status=UserWordStatus.Status.LEARNING,
            wrong_count=2,
        )

        response = self.client.post(
            reverse("quizzes:toggle_saved", args=[self.items[0].pk]),
            HTTP_HX_REQUEST="true",
        )

        status.refresh_from_db()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Saved")
        self.assertTrue(status.is_saved)
        self.assertEqual(status.status, UserWordStatus.Status.LEARNING)
        self.assertEqual(status.wrong_count, 2)

    def test_bookmark_toggle_rejects_another_users_word(self):
        response = self.client.post(
            reverse("quizzes:toggle_saved", args=[self.outsider.pk]),
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 404)
        self.assertFalse(
            UserWordStatus.objects.filter(
                user=self.user,
                vocabulary_item=self.outsider,
            ).exists()
        )

    def test_saved_words_list_is_owner_scoped(self):
        saved = UserWordStatus.objects.create(
            user=self.user,
            vocabulary_item=self.items[0],
            is_saved=True,
        )
        UserWordStatus.objects.create(
            user=self.user,
            vocabulary_item=self.items[1],
            is_saved=False,
        )
        UserWordStatus.objects.create(
            user=self.other_user,
            vocabulary_item=self.outsider,
            is_saved=True,
        )

        response = self.client.get(reverse("quizzes:saved_words"))

        self.assertQuerySetEqual(response.context["word_statuses"], [saved])
        self.assertContains(response, self.items[0].word_or_phrase)
        self.assertNotContains(response, self.items[1].word_or_phrase)
        self.assertNotContains(response, self.outsider.word_or_phrase)

    def test_feedback_next_link_preserves_movie_filter(self):
        question = generate_question(
            user=self.user,
            target=self.items[0],
            movie_ids=(self.movie.pk,),
        )

        response = self.client.post(
            reverse("quizzes:answer", args=["collection"]),
            {
                "question_token": question.token,
                "selected_option": self.items[0].pk,
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertContains(response, f"movies={self.movie.pk}")

    def test_targeted_feedback_is_neutral_and_preserves_existing_progress(self):
        existing_status = UserWordStatus.objects.create(
            user=self.user,
            vocabulary_item=self.items[0],
            status=UserWordStatus.Status.MASTERED,
            correct_count=3,
            wrong_count=1,
        )
        filter_spec = VocabularyFilterSpec(movie_id=self.movie.pk)
        question = generate_question(
            user=self.user,
            pool=TARGETED_POOL,
            mode=DEFINITION_MODE,
            target=self.items[0],
            filter_spec=filter_spec,
        )
        wrong_id = next(
            option.vocabulary_item_id
            for option in question.options
            if option.vocabulary_item_id != question.target.pk
        )

        response = self.client.post(
            reverse("quizzes:answer", args=[TARGETED_POOL]),
            {
                "question_token": question.token,
                "selected_option": wrong_id,
            },
            HTTP_HX_REQUEST="true",
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Not quite")
        self.assertContains(response, "Free practice · Progress unchanged")
        self.assertNotContains(response, "Added to Learning Pool")
        self.assertNotContains(response, "Study Learning Pool")
        existing_status.refresh_from_db()
        self.assertEqual(existing_status.status, UserWordStatus.Status.MASTERED)
        self.assertEqual(existing_status.correct_count, 3)
        self.assertEqual(existing_status.wrong_count, 1)
        self.assertIsNone(existing_status.last_tested_at)

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
