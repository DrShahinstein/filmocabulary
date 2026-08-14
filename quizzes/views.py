from urllib.parse import urlencode

from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q
from django.http import HttpResponse, HttpResponseNotAllowed
from django.shortcuts import render
from django.urls import reverse
from django.views import View
from django.views.generic import ListView, TemplateView

from vocabulary.models import VocabularyItem

from .forms import PracticeSetupForm, QuizAnswerForm
from .models import UserWordStatus
from .services import (
    DuplicateAnswerError,
    QuizTokenError,
    QuizUnavailableError,
    answer_question,
    generate_question,
    question_from_token,
    skip_question,
    toggle_saved_word,
)


def _is_htmx(request):
    return request.headers.get("HX-Request") == "true"


def _question_url(pool, movie_ids=()):
    url = reverse("quizzes:question", args=[pool])
    query = urlencode([("movies", movie_id) for movie_id in movie_ids])
    return f"{url}?{query}" if query else url


def _question_context(*, pool, question=None, unavailable_message=None):
    context = {"pool": pool}
    if question is not None:
        context.update(
            {
                "question": question,
                "form": QuizAnswerForm(question=question),
                "next_question_url": _question_url(pool, question.movie_ids),
            }
        )
    if unavailable_message is not None:
        context["unavailable_message"] = unavailable_message
    return context


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
        setup_form = PracticeSetupForm(
            {"movies": request.GET.getlist("movies")},
            user=request.user,
        )
        if not setup_form.is_valid():
            context = _question_context(
                pool=pool,
                unavailable_message="Choose valid movies from your library.",
            )
            template = (
                "partials/mcq_question.html"
                if _is_htmx(request)
                else "quizzes/practice.html"
            )
            return render(request, template, context, status=400)
        movie_ids = tuple(
            setup_form.cleaned_data["movies"].values_list("pk", flat=True)
        )
        try:
            question = generate_question(
                user=request.user,
                pool=pool,
                movie_ids=movie_ids,
            )
        except QuizUnavailableError as exc:
            context = _question_context(pool=pool, unavailable_message=str(exc))
        else:
            context = _question_context(pool=pool, question=question)

        template = (
            "partials/mcq_question.html"
            if _is_htmx(request)
            else "quizzes/practice.html"
        )
        return render(request, template, context)


class QuizSkipView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        return HttpResponseNotAllowed(["POST"])

    def post(self, request, *args, **kwargs):
        pool = kwargs["pool"]
        try:
            question = skip_question(
                user=request.user,
                token=request.POST.get("question_token", ""),
                expected_pool=pool,
            )
        except QuizTokenError as exc:
            context = _question_context(pool=pool, unavailable_message=str(exc))
            status = 400
        except QuizUnavailableError as exc:
            context = _question_context(pool=pool, unavailable_message=str(exc))
            status = 200
        else:
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
                {"pool": kwargs["pool"], "unavailable_message": str(exc)},
                status=400,
            )

        if question.pool != kwargs["pool"]:
            return render(
                request,
                "partials/mcq_question.html",
                {
                    "pool": kwargs["pool"],
                    "unavailable_message": "This question belongs to a different practice pool.",
                },
                status=400,
            )

        form = QuizAnswerForm(request.POST, question=question)
        if not form.is_valid():
            return render(
                request,
                "partials/mcq_question.html",
                {"pool": question.pool, "question": question, "form": form},
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
                {"pool": question.pool, "unavailable_message": str(exc)},
                status=400,
            )

        context = {
            "result": result,
            "pool": question.pool,
            "next_question_url": _question_url(
                question.pool,
                result.question.movie_ids,
            ),
        }
        template = (
            "partials/mcq_feedback.html"
            if _is_htmx(request)
            else "quizzes/feedback.html"
        )
        return render(request, template, context)
