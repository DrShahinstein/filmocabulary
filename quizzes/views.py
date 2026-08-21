from urllib.parse import urlencode

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q
from django.http import HttpResponse, HttpResponseNotAllowed
from django.shortcuts import render
from django.urls import reverse
from django.views import View
from django.views.generic import ListView, TemplateView

from vocabulary.models import VocabularyItem

from .forms import (
    PracticeSetupForm,
    QuizAnswerForm,
    QuizContinuationForm,
    TargetedPracticeLaunchForm,
)
from .models import UserWordStatus
from .services import (
    DuplicateAnswerError,
    QuizTokenError,
    QuizUnavailableError,
    TARGETED_POOL,
    answer_question,
    generate_question,
    question_from_token,
    skip_question,
    targeted_scope_from_token,
    toggle_saved_word,
)


def _is_htmx(request):
    return request.headers.get("HX-Request") == "true"


def _question_url(
    pool,
    movie_ids=(),
    *,
    mode="definition",
    scope_token=None,
    continuation_token=None,
):
    url = reverse("quizzes:question", args=[pool])
    parameters = [("mode", mode)]
    if continuation_token:
        parameters.append(("continue", continuation_token))
    if pool == TARGETED_POOL:
        if scope_token:
            parameters.append(("scope", scope_token))
    else:
        parameters.extend(("movies", movie_id) for movie_id in movie_ids)
    query = urlencode(parameters)
    return f"{url}?{query}"


def _explorer_url(filter_spec=None):
    url = reverse("words:index")
    if filter_spec is None:
        return url
    query = filter_spec.as_query_string()
    return f"{url}?{query}" if query else url


def _practice_metadata(*, pool, filter_spec=None):
    if pool == TARGETED_POOL:
        return {
            "practice_label": "Filtered Words Explorer practice",
            "finish_url": _explorer_url(filter_spec),
            "finish_label": "Back to Words Explorer",
        }
    if pool == "learning":
        return {
            "practice_label": "Learning Pool",
            "finish_url": reverse("quizzes:learning_pool"),
            "finish_label": "Finish",
        }
    return {
        "practice_label": "Collection",
        "finish_url": reverse("quizzes:dashboard"),
        "finish_label": "Finish",
    }


def _answer_form(question, data=None):
    if data is None:
        return QuizAnswerForm(question=question)
    return QuizAnswerForm(data, question=question)


def _question_context(
    *,
    pool,
    question=None,
    unavailable_message=None,
    filter_spec=None,
):
    if question is not None:
        filter_spec = question.filter_spec
    context = {
        "pool": pool,
        **_practice_metadata(pool=pool, filter_spec=filter_spec),
    }
    if question is not None:
        context.update(
            {
                "question": question,
                "form": _answer_form(question),
                "next_question_url": _question_url(
                    pool,
                    question.movie_ids,
                    mode=question.mode,
                    scope_token=question.scope_token,
                    continuation_token=question.token,
                ),
            }
        )
    if unavailable_message is not None:
        context["unavailable_message"] = unavailable_message
    return context


QUIZ_RUNS_SESSION_KEY = "quiz_run_histories"
MAX_QUIZ_RUNS = 12


def _clean_quiz_run_histories(request):
    raw_histories = request.session.get(QUIZ_RUNS_SESSION_KEY, {})
    if not isinstance(raw_histories, dict):
        return {}

    histories = {}
    for run_id, raw_history in raw_histories.items():
        if not isinstance(run_id, str) or not isinstance(raw_history, list):
            continue
        history = []
        seen = set()
        for item_id in raw_history:
            if (
                isinstance(item_id, int)
                and not isinstance(item_id, bool)
                and item_id > 0
                and item_id not in seen
            ):
                history.append(item_id)
                seen.add(item_id)
        histories[run_id] = history
    return histories


def _question_history(request, question):
    if question.run_id is None:
        return (question.target.pk,)
    history = list(
        _clean_quiz_run_histories(request).get(question.run_id, [])
    )
    if question.target.pk in history:
        history.remove(question.target.pk)
    history.append(question.target.pk)
    return tuple(history)


def _record_question(request, question):
    if question.run_id is None:
        return
    histories = _clean_quiz_run_histories(request)
    history = [] if question.round_reset else histories.pop(question.run_id, [])
    if question.target.pk in history:
        history.remove(question.target.pk)
    history.append(question.target.pk)
    histories[question.run_id] = history
    while len(histories) > MAX_QUIZ_RUNS:
        histories.pop(next(iter(histories)))
    request.session[QUIZ_RUNS_SESSION_KEY] = histories


class ProgressDashboardView(LoginRequiredMixin, TemplateView):
    template_name = "quizzes/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        total_words = VocabularyItem.objects.filter(movie__user=self.request.user).count()
        status_counts = UserWordStatus.objects.filter(
            user=self.request.user,
            vocabulary_item__movie__user=self.request.user,
        ).aggregate(
            learning=Count("pk", filter=Q(status=UserWordStatus.Status.LEARNING)),
            mastered=Count("pk", filter=Q(status=UserWordStatus.Status.MASTERED)),
        )
        context["progress"] = {
            "total": total_words,
            "new": max(
                total_words - status_counts["learning"] - status_counts["mastered"],
                0,
            ),
            **status_counts,
        }
        context["saved_count"] = UserWordStatus.objects.filter(
            user=self.request.user,
            vocabulary_item__movie__user=self.request.user,
            is_saved=True,
        ).count()
        context["practice_form"] = PracticeSetupForm(user=self.request.user)
        return context


class LearningPoolView(LoginRequiredMixin, ListView):
    template_name = "quizzes/learning_pool.html"
    context_object_name = "word_statuses"

    def get_queryset(self):
        return (
            UserWordStatus.objects.filter(
                user=self.request.user,
                vocabulary_item__movie__user=self.request.user,
                status=UserWordStatus.Status.LEARNING,
            )
            .select_related("vocabulary_item", "vocabulary_item__movie")
            .order_by(
                "-wrong_count",
                "last_tested_at",
                "vocabulary_item__word_or_phrase",
            )
        )


class SavedWordsView(LoginRequiredMixin, ListView):
    template_name = "quizzes/saved_words.html"
    context_object_name = "word_statuses"

    def get_queryset(self):
        return (
            UserWordStatus.objects.filter(
                user=self.request.user,
                vocabulary_item__movie__user=self.request.user,
                is_saved=True,
            )
            .select_related("vocabulary_item", "vocabulary_item__movie")
            .order_by(
                "vocabulary_item__movie__title",
                "vocabulary_item__word_or_phrase",
                "pk",
            )
        )


class QuizQuestionView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        pool = kwargs["pool"]
        filter_spec = None

        if "continue" in request.GET:
            continuation_form = QuizContinuationForm(
                {"token": request.GET.get("continue", "")}
            )
            current = None
            try:
                if not continuation_form.is_valid():
                    raise QuizTokenError(
                        "This question is invalid. Load a new one to continue."
                    )
                current = question_from_token(
                    user=request.user,
                    token=continuation_form.cleaned_data["token"],
                )
                if current.pool != pool:
                    raise QuizTokenError(
                        "This question belongs to a different practice pool."
                    )
                question = skip_question(
                    user=request.user,
                    token=current.token,
                    expected_pool=pool,
                    excluded_target_ids=_question_history(request, current),
                )
            except QuizTokenError as exc:
                context = _question_context(
                    pool=pool,
                    filter_spec=current.filter_spec if current else None,
                    unavailable_message=str(exc),
                )
                return self._render(request, context, status=400)
            except QuizUnavailableError as exc:
                context = _question_context(
                    pool=pool,
                    filter_spec=current.filter_spec if current else None,
                    unavailable_message=str(exc),
                )
                return self._render(request, context)
            _record_question(request, question)
            return self._render(
                request,
                _question_context(pool=pool, question=question),
            )

        if pool == TARGETED_POOL:
            launch_form = TargetedPracticeLaunchForm(request.GET)
            if not launch_form.is_valid():
                context = _question_context(
                    pool=pool,
                    unavailable_message=(
                        "Return to Words Explorer and choose Definition or "
                        "Fill-in-the-blanks practice."
                    ),
                )
                return self._render(request, context, status=400)
            try:
                filter_spec = targeted_scope_from_token(
                    user=request.user,
                    token=launch_form.cleaned_data["scope"],
                )
                question = generate_question(
                    user=request.user,
                    pool=pool,
                    mode=launch_form.cleaned_data["mode"],
                    filter_spec=filter_spec,
                    scope_token=launch_form.cleaned_data["scope"],
                )
            except QuizTokenError as exc:
                context = _question_context(
                    pool=pool,
                    unavailable_message=str(exc),
                )
                return self._render(request, context, status=400)
            except QuizUnavailableError as exc:
                context = _question_context(
                    pool=pool,
                    filter_spec=filter_spec,
                    unavailable_message=str(exc),
                )
            else:
                _record_question(request, question)
                context = _question_context(pool=pool, question=question)
            return self._render(request, context)

        setup_form = PracticeSetupForm(
            {
                "movies": request.GET.getlist("movies"),
                "mode": request.GET.get("mode", ""),
            },
            user=request.user,
        )
        if not setup_form.is_valid():
            context = _question_context(
                pool=pool,
                unavailable_message="Choose valid movies from your library.",
            )
            return self._render(request, context, status=400)
        movie_ids = tuple(
            setup_form.cleaned_data["movies"].values_list("pk", flat=True)
        )
        try:
            question = generate_question(
                user=request.user,
                pool=pool,
                mode=setup_form.cleaned_data["mode"],
                movie_ids=movie_ids,
            )
        except QuizUnavailableError as exc:
            context = _question_context(pool=pool, unavailable_message=str(exc))
        else:
            _record_question(request, question)
            context = _question_context(pool=pool, question=question)
        return self._render(request, context)

    @staticmethod
    def _render(request, context, status=200):
        template = (
            "partials/mcq_question.html"
            if _is_htmx(request)
            else "quizzes/practice.html"
        )
        return render(request, template, context, status=status)


class QuizSkipView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        return HttpResponseNotAllowed(["POST"])

    def post(self, request, *args, **kwargs):
        pool = kwargs["pool"]
        current = None
        try:
            current = question_from_token(
                user=request.user,
                token=request.POST.get("question_token", ""),
            )
            if current.pool != pool:
                raise QuizTokenError("This question belongs to a different practice pool.")
            question = skip_question(
                user=request.user,
                token=current.token,
                expected_pool=pool,
                excluded_target_ids=_question_history(request, current),
            )
        except QuizTokenError as exc:
            context = _question_context(
                pool=pool,
                filter_spec=current.filter_spec if current else None,
                unavailable_message=str(exc),
            )
            status = 400
        except QuizUnavailableError as exc:
            context = _question_context(
                pool=pool,
                filter_spec=current.filter_spec if current else None,
                unavailable_message=str(exc),
            )
            status = 200
        else:
            _record_question(request, question)
            context = _question_context(pool=pool, question=question)
            status = 200

        template = (
            "partials/mcq_question.html"
            if _is_htmx(request)
            else "quizzes/practice.html"
        )
        return render(request, template, context, status=status)


class SavedWordToggleView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        return HttpResponseNotAllowed(["POST"])

    def post(self, request, *args, **kwargs):
        try:
            item, word_status = toggle_saved_word(
                user=request.user,
                vocabulary_item_id=kwargs["item_id"],
            )
        except VocabularyItem.DoesNotExist:
            return HttpResponse(status=404)

        if request.POST.get("context") == "saved-list":
            if not word_status.is_saved:
                return HttpResponse("")
            return render(
                request,
                "partials/saved_word_card.html",
                {"word_status": word_status},
            )
        return render(
            request,
            "partials/bookmark_button.html",
            {"item": item, "is_saved": word_status.is_saved},
        )


class QuizAnswerView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        return HttpResponseNotAllowed(["POST"])

    def post(self, request, *args, **kwargs):
        token = request.POST.get("question_token", "")
        try:
            question = question_from_token(user=request.user, token=token)
        except QuizTokenError as exc:
            return render(
                request,
                "partials/mcq_question.html",
                _question_context(
                    pool=kwargs["pool"],
                    unavailable_message=str(exc),
                ),
                status=400,
            )

        if question.pool != kwargs["pool"]:
            return render(
                request,
                "partials/mcq_question.html",
                _question_context(
                    pool=kwargs["pool"],
                    unavailable_message=(
                        "This question belongs to a different practice pool."
                    ),
                ),
                status=400,
            )

        form = _answer_form(question, request.POST)
        if not form.is_valid():
            context = {
                **_question_context(pool=question.pool, question=question),
                "form": form,
            }
            return render(
                request,
                "partials/mcq_question.html",
                context,
                status=422,
            )

        try:
            result = answer_question(
                user=request.user,
                token=form.cleaned_data["question_token"],
                selected_item_id=form.cleaned_data["selected_option"],
            )
        except (QuizTokenError, DuplicateAnswerError) as exc:
            return render(
                request,
                "partials/mcq_question.html",
                _question_context(
                    pool=question.pool,
                    filter_spec=question.filter_spec,
                    unavailable_message=str(exc),
                ),
                status=400,
            )

        context = {
            "result": result,
            "pool": question.pool,
            "next_question_url": _question_url(
                question.pool,
                result.question.movie_ids,
                mode=result.question.mode,
                scope_token=result.question.scope_token,
                continuation_token=result.question.token,
            ),
            **_practice_metadata(
                pool=question.pool,
                filter_spec=question.filter_spec,
            ),
        }
        template = (
            "partials/mcq_feedback.html"
            if _is_htmx(request)
            else "quizzes/feedback.html"
        )
        return render(request, template, context)
