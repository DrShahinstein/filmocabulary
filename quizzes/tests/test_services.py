import random
from unittest.mock import patch

from django.core import signing
from django.core.cache import cache
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase

from quizzes.models import UserWordStatus
from quizzes.services import (
    CLOZE_MODE,
    DEFINITION_MODE,
    MIXED_MODE,
    DuplicateAnswerError,
    QUESTION_SALT,
    TARGETED_POOL,
    QuizTokenError,
    QuizUnavailableError,
    answer_question,
    generate_question,
    question_from_token,
    sign_targeted_scope,
    skip_question,
    targeted_scope_from_token,
    toggle_saved_word,
)
from vocabulary.models import VocabularyItem
from vocabulary.querysets import VocabularyFilterSpec

from .factories import make_movie, make_user, make_vocabulary


class QuizEngineTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = make_user("engine-learner")
        self.movie = make_movie(self.user)
        self.verbs = [
            make_vocabulary(
                self.movie,
                word=f"verb-{index}",
                definition=f"Verb meaning {index}.",
                word_type="verb",
            )
            for index in range(5)
        ]
        self.nouns = [
            make_vocabulary(
                self.movie,
                word=f"noun-{index}",
                definition=f"Noun meaning {index}.",
                word_type="noun",
            )
            for index in range(2)
        ]

    def test_generator_builds_five_unique_options_with_one_correct_definition(self):
        target = self.verbs[0]

        question = generate_question(
            user=self.user,
            target=target,
            rng=random.Random(4),
        )

        self.assertEqual(len(question.options), 5)
        self.assertEqual(len({option.definition for option in question.options}), 5)
        self.assertEqual(
            sum(option.vocabulary_item_id == target.pk for option in question.options),
            1,
        )
        decoded = question_from_token(user=self.user, token=question.token)
        self.assertEqual(decoded.target, target)

    def test_distractors_prioritize_the_same_part_of_speech(self):
        target = self.verbs[0]

        question = generate_question(
            user=self.user,
            target=target,
            rng=random.Random(2),
        )

        option_ids = {option.vocabulary_item_id for option in question.options}
        self.assertEqual(option_ids, {item.pk for item in self.verbs})

    def test_distractors_fall_back_to_other_parts_of_speech(self):
        target = self.nouns[0]

        question = generate_question(
            user=self.user,
            target=target,
            rng=random.Random(5),
        )

        self.assertEqual(len(question.options), 5)
        self.assertIn(self.nouns[1].pk, {option.vocabulary_item_id for option in question.options})

    def test_options_never_include_another_users_vocabulary(self):
        other_user = make_user("engine-outsider")
        other_movie = make_movie(other_user, "Heat", 1995)
        outsider = make_vocabulary(other_movie, word="outsider")

        question = generate_question(user=self.user, target=self.verbs[0])

        self.assertNotIn(
            outsider.pk,
            {option.vocabulary_item_id for option in question.options},
        )

    def test_correct_answer_is_shuffled_across_positions(self):
        positions = {
            next(
                index
                for index, option in enumerate(
                    generate_question(
                        user=self.user,
                        target=self.verbs[0],
                        rng=random.Random(seed),
                    ).options
                )
                if option.vocabulary_item_id == self.verbs[0].pk
            )
            for seed in range(8)
        }

        self.assertGreater(len(positions), 1)

    def test_generator_requires_five_distinct_definitions(self):
        small_user = make_user("small-library")
        movie = make_movie(small_user, "Primer", 2004)
        for index in range(4):
            make_vocabulary(movie, word=f"term-{index}")

        with self.assertRaisesMessage(QuizUnavailableError, "at least five"):
            generate_question(user=small_user)

    def test_learning_pool_only_targets_learning_words(self):
        learning = self.verbs[2]
        UserWordStatus.objects.create(
            user=self.user,
            vocabulary_item=learning,
            status=UserWordStatus.Status.LEARNING,
            wrong_count=1,
        )

        question = generate_question(user=self.user, pool="learning")

        self.assertEqual(question.target, learning)
        self.assertEqual(question.pool, "learning")

    def test_collection_prioritizes_unencountered_words(self):
        for item in [*self.verbs, *self.nouns[:-1]]:
            UserWordStatus.objects.create(
                user=self.user,
                vocabulary_item=item,
                status=UserWordStatus.Status.MASTERED,
            )

        question = generate_question(user=self.user, pool="collection")

        self.assertEqual(question.target, self.nouns[-1])

    def test_empty_learning_pool_has_clear_error(self):
        with self.assertRaisesMessage(QuizUnavailableError, "Learning Pool is empty"):
            generate_question(user=self.user, pool="learning")

    def test_movie_filter_scopes_target_distractors_and_signed_question(self):
        selected_movie = make_movie(self.user, "The Matrix", 1999)
        selected_items = [
            make_vocabulary(
                selected_movie,
                word=f"matrix-{index}",
                definition=f"Matrix meaning {index}.",
            )
            for index in range(5)
        ]

        question = generate_question(
            user=self.user,
            movie_ids=(selected_movie.pk,),
            rng=random.Random(4),
        )
        decoded = question_from_token(user=self.user, token=question.token)

        self.assertEqual(question.movie_ids, (selected_movie.pk,))
        self.assertEqual(decoded.movie_ids, (selected_movie.pk,))
        self.assertEqual(question.target.movie, selected_movie)
        self.assertEqual(
            {option.vocabulary_item_id for option in question.options},
            {item.pk for item in selected_items},
        )

        following = skip_question(user=self.user, token=question.token)
        self.assertNotEqual(following.target, question.target)
        self.assertEqual(following.movie_ids, (selected_movie.pk,))
        self.assertEqual(following.target.movie, selected_movie)

    def test_movie_filter_rejects_another_users_movie(self):
        other_user = make_user("movie-filter-outsider")
        other_movie = make_movie(other_user, "Heat", 1995)

        with self.assertRaisesMessage(QuizUnavailableError, "valid movies"):
            generate_question(user=self.user, movie_ids=(other_movie.pk,))

    def test_skip_changes_target_without_creating_or_updating_status(self):
        current = generate_question(
            user=self.user,
            target=self.verbs[0],
            rng=random.Random(2),
        )

        following = skip_question(user=self.user, token=current.token)

        self.assertNotEqual(following.target, current.target)
        self.assertFalse(UserWordStatus.objects.filter(user=self.user).exists())


class ClozeQuizTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = make_user("cloze-learner")
        self.movie = make_movie(self.user)

    def test_cloze_question_does_not_require_five_vocabulary_items(self):
        item = make_vocabulary(
            self.movie,
            word="scrutinize",
            sentence="The reporter will ___ every detail.",
        )

        question = generate_question(user=self.user, mode=CLOZE_MODE)
        decoded = question_from_token(user=self.user, token=question.token)

        self.assertEqual(question.target, item)
        self.assertEqual(question.kind, CLOZE_MODE)
        self.assertEqual(question.options, ())
        self.assertEqual(decoded.target, item)
        self.assertEqual(decoded.kind, CLOZE_MODE)

    def test_inflected_cloze_answer_updates_tracked_progress(self):
        item = VocabularyItem.objects.create(
            movie=self.movie,
            word_or_phrase="scrutinize",
            type=VocabularyItem.Type.VERB,
            cefr_level=VocabularyItem.CefrLevel.C1,
            definition_en="Examine something very carefully.",
            example_sentence="The reporter scrutinized every detail.",
            blank_sentence="The reporter ___ every detail.",
        )
        correct_question = generate_question(
            user=self.user,
            mode=CLOZE_MODE,
            target=item,
        )

        correct_result = answer_question(
            user=self.user,
            token=correct_question.token,
            submitted_answer="  SCRUTINIZED  ",
        )

        status = UserWordStatus.objects.get(user=self.user, vocabulary_item=item)
        self.assertTrue(correct_result.is_correct)
        self.assertTrue(correct_result.updates_progress)
        self.assertEqual(correct_result.submitted_answer, "SCRUTINIZED")
        self.assertEqual(correct_result.correct_answer, "scrutinized")
        self.assertEqual(status.status, UserWordStatus.Status.MASTERED)
        self.assertEqual(status.correct_count, 1)
        self.assertEqual(status.wrong_count, 0)
        self.assertIsNotNone(status.last_tested_at)

        wrong_question = generate_question(
            user=self.user,
            mode=CLOZE_MODE,
            target=item,
        )
        wrong_result = answer_question(
            user=self.user,
            token=wrong_question.token,
            submitted_answer="reviewed",
        )

        status.refresh_from_db()
        self.assertFalse(wrong_result.is_correct)
        self.assertEqual(status.status, UserWordStatus.Status.LEARNING)
        self.assertEqual(status.correct_count, 1)
        self.assertEqual(status.wrong_count, 1)

    def test_separable_phrasal_verb_accepts_inflected_answer(self):
        item = VocabularyItem.objects.create(
            movie=self.movie,
            word_or_phrase="brush off",
            type=VocabularyItem.Type.PHRASAL_VERB,
            cefr_level=VocabularyItem.CefrLevel.C1,
            definition_en="Dismiss something as unimportant.",
            example_sentence="She brushed the criticism off immediately.",
            blank_sentence="She ___ the criticism ___ immediately.",
        )
        question = generate_question(
            user=self.user,
            mode=CLOZE_MODE,
            target=item,
        )

        result = answer_question(
            user=self.user,
            token=question.token,
            submitted_answer="brushed off",
        )

        self.assertTrue(result.is_correct)
        self.assertEqual(result.correct_answer, "brushed off")
        self.assertEqual(
            UserWordStatus.objects.get(user=self.user, vocabulary_item=item).status,
            UserWordStatus.Status.MASTERED,
        )

    def test_mixed_mode_selects_both_kinds_and_skip_preserves_mode(self):
        items = [
            make_vocabulary(
                self.movie,
                word=f"mixed-{index}",
                definition=f"Mixed meaning {index}.",
            )
            for index in range(5)
        ]

        cloze_question = generate_question(
            user=self.user,
            mode=MIXED_MODE,
            target=items[0],
            rng=random.Random(0),
        )
        definition_question = generate_question(
            user=self.user,
            mode=MIXED_MODE,
            target=items[0],
            rng=random.Random(1),
        )
        following = skip_question(user=self.user, token=cloze_question.token)

        self.assertEqual(cloze_question.kind, CLOZE_MODE)
        self.assertEqual(definition_question.kind, DEFINITION_MODE)
        self.assertEqual(cloze_question.mode, MIXED_MODE)
        self.assertEqual(definition_question.mode, MIXED_MODE)
        self.assertEqual(following.mode, MIXED_MODE)
        self.assertNotEqual(following.target, cloze_question.target)
        self.assertEqual(
            question_from_token(user=self.user, token=following.token).mode,
            MIXED_MODE,
        )


class TargetedPracticeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = make_user("targeted-learner")
        self.movie = make_movie(self.user)
        self.distractors = [
            make_vocabulary(
                self.movie,
                word=f"distractor-{index}",
                definition=f"Distractor meaning {index}.",
            )
            for index in range(5)
        ]

    def test_signed_scope_is_user_bound_and_round_trips_exact_filters(self):
        spec = VocabularyFilterSpec(
            q="distractor",
            word_type=VocabularyItem.Type.ADJECTIVE,
            movie_id=self.movie.pk,
            cefr_levels=(VocabularyItem.CefrLevel.C1,),
        )
        token = sign_targeted_scope(user=self.user, filter_spec=spec)
        other_user = make_user("targeted-scope-outsider")

        self.assertEqual(
            targeted_scope_from_token(user=self.user, token=token),
            spec,
        )
        with self.assertRaises(QuizTokenError):
            targeted_scope_from_token(user=other_user, token=token)
        with self.assertRaises(QuizTokenError):
            targeted_scope_from_token(user=self.user, token=f"{token}x")

    def test_targeted_definition_uses_exact_filter_and_does_not_mutate_status(self):
        target = VocabularyItem.objects.create(
            movie=self.movie,
            word_or_phrase="read between the lines",
            type=VocabularyItem.Type.IDIOM,
            cefr_level=VocabularyItem.CefrLevel.C2,
            definition_en="Infer a concealed meaning.",
            example_sentence="She could read between the lines of his denial.",
            blank_sentence="She could ___ of his denial.",
        )
        status = UserWordStatus.objects.create(
            user=self.user,
            vocabulary_item=target,
            status=UserWordStatus.Status.MASTERED,
            is_saved=True,
            correct_count=7,
            wrong_count=3,
        )
        make_vocabulary(
            self.movie,
            word="read carefully",
            definition="Examine written material with care.",
            word_type=VocabularyItem.Type.VERB,
            cefr_level=VocabularyItem.CefrLevel.C2,
        )
        spec = VocabularyFilterSpec(
            q="read",
            status=UserWordStatus.Status.MASTERED,
            word_type=VocabularyItem.Type.IDIOM,
            movie_id=self.movie.pk,
            cefr_levels=(VocabularyItem.CefrLevel.C2,),
        )
        scope_token = sign_targeted_scope(user=self.user, filter_spec=spec)

        question = generate_question(
            user=self.user,
            pool=TARGETED_POOL,
            mode=DEFINITION_MODE,
            scope_token=scope_token,
            rng=random.Random(4),
        )
        wrong_id = next(
            option.vocabulary_item_id
            for option in question.options
            if option.vocabulary_item_id != target.pk
        )
        result = answer_question(
            user=self.user,
            token=question.token,
            selected_item_id=wrong_id,
        )

        status.refresh_from_db()
        self.assertEqual(question.target, target)
        self.assertEqual(question.filter_spec, spec)
        self.assertFalse(result.is_correct)
        self.assertFalse(result.updates_progress)
        self.assertIsNone(result.word_status)
        self.assertEqual(status.status, UserWordStatus.Status.MASTERED)
        self.assertTrue(status.is_saved)
        self.assertEqual(status.correct_count, 7)
        self.assertEqual(status.wrong_count, 3)
        self.assertIsNone(status.last_tested_at)

    def test_targeted_cloze_does_not_create_status_and_question_is_owner_bound(self):
        target = make_vocabulary(
            self.movie,
            word="untracked-target",
            definition="A word used only for targeted practice.",
            sentence="The ___ remained untracked.",
        )
        spec = VocabularyFilterSpec(q="untracked-target")
        scope_token = sign_targeted_scope(user=self.user, filter_spec=spec)
        question = generate_question(
            user=self.user,
            pool=TARGETED_POOL,
            mode=CLOZE_MODE,
            scope_token=scope_token,
        )

        result = answer_question(
            user=self.user,
            token=question.token,
            submitted_answer="untracked-target",
        )

        self.assertEqual(question.target, target)
        self.assertTrue(result.is_correct)
        self.assertFalse(result.updates_progress)
        self.assertIsNone(result.word_status)
        self.assertFalse(
            UserWordStatus.objects.filter(user=self.user, vocabulary_item=target).exists()
        )

        other_user = make_user("targeted-question-outsider")
        with self.assertRaises(QuizTokenError):
            question_from_token(user=other_user, token=question.token)


class AnswerTrackingTests(TestCase):
    def setUp(self):
        cache.clear()
        self.addCleanup(cache.clear)
        self.user = make_user("answer-learner")
        movie = make_movie(self.user)
        self.items = [
            make_vocabulary(
                movie,
                word=f"word-{index}",
                definition=f"Meaning {index}.",
            )
            for index in range(5)
        ]

    def test_wrong_answer_creates_learning_status_and_increments_wrong_count(self):
        question = generate_question(user=self.user, target=self.items[0])
        wrong_option = next(
            option
            for option in question.options
            if option.vocabulary_item_id != question.target.pk
        )

        result = answer_question(
            user=self.user,
            token=question.token,
            selected_item_id=wrong_option.vocabulary_item_id,
        )

        self.assertFalse(result.is_correct)
        self.assertEqual(result.word_status.status, UserWordStatus.Status.LEARNING)
        self.assertEqual(result.word_status.wrong_count, 1)
        self.assertEqual(result.word_status.correct_count, 0)
        self.assertIsNotNone(result.word_status.last_tested_at)

    def test_correct_answer_promotes_word_to_mastered(self):
        status = UserWordStatus.objects.create(
            user=self.user,
            vocabulary_item=self.items[0],
            status=UserWordStatus.Status.LEARNING,
            is_saved=True,
            wrong_count=2,
        )
        question = generate_question(
            user=self.user,
            pool="learning",
            target=self.items[0],
        )

        result = answer_question(
            user=self.user,
            token=question.token,
            selected_item_id=self.items[0].pk,
        )

        status.refresh_from_db()
        self.assertTrue(result.is_correct)
        self.assertEqual(status.status, UserWordStatus.Status.MASTERED)
        self.assertEqual(status.correct_count, 1)
        self.assertEqual(status.wrong_count, 2)
        self.assertTrue(status.is_saved)

    def test_wrong_answer_demotes_mastered_word_to_learning(self):
        status = UserWordStatus.objects.create(
            user=self.user,
            vocabulary_item=self.items[0],
            status=UserWordStatus.Status.MASTERED,
            correct_count=1,
        )
        question = generate_question(user=self.user, target=self.items[0])
        wrong_id = next(
            option.vocabulary_item_id
            for option in question.options
            if option.vocabulary_item_id != self.items[0].pk
        )

        answer_question(user=self.user, token=question.token, selected_item_id=wrong_id)

        status.refresh_from_db()
        self.assertEqual(status.status, UserWordStatus.Status.LEARNING)
        self.assertEqual(status.wrong_count, 1)

    def test_question_cannot_be_scored_twice(self):
        question = generate_question(user=self.user, target=self.items[0])
        answer_question(
            user=self.user,
            token=question.token,
            selected_item_id=self.items[0].pk,
        )

        with self.assertRaises(DuplicateAnswerError):
            answer_question(
                user=self.user,
                token=question.token,
                selected_item_id=self.items[0].pk,
            )

        self.assertEqual(
            UserWordStatus.objects.get(user=self.user).correct_count,
            1,
        )

    def test_tampered_question_is_rejected(self):
        question = generate_question(user=self.user)
        tampered = f"{question.token}x"

        with self.assertRaises(QuizTokenError):
            answer_question(
                user=self.user,
                token=tampered,
                selected_item_id=self.items[0].pk,
            )

    def test_failed_status_write_does_not_consume_question(self):
        question = generate_question(user=self.user, target=self.items[0])
        with patch.object(
            UserWordStatus,
            "save",
            side_effect=IntegrityError("write failed"),
        ):
            with self.assertRaises(IntegrityError):
                answer_question(
                    user=self.user,
                    token=question.token,
                    selected_item_id=self.items[0].pk,
                )

        result = answer_question(
            user=self.user,
            token=question.token,
            selected_item_id=self.items[0].pk,
        )

        self.assertTrue(result.is_correct)

    def test_signed_question_cannot_reference_another_users_word(self):
        other = make_user("answer-outsider")
        other_movie = make_movie(other, "Heat", 1995)
        outsider = make_vocabulary(other_movie, word="outsider")
        option_ids = [item.pk for item in self.items[:4]] + [outsider.pk]
        token = signing.dumps(
            {
                "target": self.items[0].pk,
                "options": option_ids,
                "pool": "collection",
            },
            salt=QUESTION_SALT,
        )

        with self.assertRaises(QuizTokenError):
            question_from_token(user=self.user, token=token)


class UserWordStatusModelTests(TestCase):
    def setUp(self):
        self.user = make_user("model-learner")
        self.other = make_user("model-other")
        self.movie = make_movie(self.user)
        self.item = make_vocabulary(self.movie)

    def test_user_and_vocabulary_item_are_unique(self):
        UserWordStatus.objects.create(user=self.user, vocabulary_item=self.item)

        with self.assertRaises(IntegrityError), transaction.atomic():
            UserWordStatus.objects.create(user=self.user, vocabulary_item=self.item)

    def test_model_validation_rejects_vocabulary_outside_users_library(self):
        status = UserWordStatus(user=self.other, vocabulary_item=self.item)

        with self.assertRaises(ValidationError):
            status.full_clean()

    def test_deleting_vocabulary_cascades_status(self):
        status = UserWordStatus.objects.create(
            user=self.user,
            vocabulary_item=self.item,
            status=UserWordStatus.Status.LEARNING,
        )

        self.item.delete()

        self.assertFalse(UserWordStatus.objects.filter(pk=status.pk).exists())

    def test_bookmark_is_orthogonal_to_learning_status(self):
        status = UserWordStatus.objects.create(
            user=self.user,
            vocabulary_item=self.item,
            status=UserWordStatus.Status.LEARNING,
            wrong_count=2,
        )

        _, saved_status = toggle_saved_word(
            user=self.user,
            vocabulary_item_id=self.item.pk,
        )

        status.refresh_from_db()
        self.assertTrue(saved_status.is_saved)
        self.assertTrue(status.is_saved)
        self.assertEqual(status.status, UserWordStatus.Status.LEARNING)
        self.assertEqual(status.wrong_count, 2)

    def test_bookmarking_new_word_creates_new_status_and_can_be_removed(self):
        _, saved_status = toggle_saved_word(
            user=self.user,
            vocabulary_item_id=self.item.pk,
        )
        _, unsaved_status = toggle_saved_word(
            user=self.user,
            vocabulary_item_id=self.item.pk,
        )

        self.assertEqual(saved_status.status, UserWordStatus.Status.NEW)
        self.assertTrue(saved_status.is_saved)
        self.assertFalse(unsaved_status.is_saved)

    def test_bookmark_rejects_vocabulary_outside_users_library(self):
        with self.assertRaises(VocabularyItem.DoesNotExist):
            toggle_saved_word(
                user=self.other,
                vocabulary_item_id=self.item.pk,
            )
