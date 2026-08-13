from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import Count, Q
from django.http import HttpResponseNotAllowed
from django.shortcuts import render
from django.views import View
from django.views.generic import ListView, TemplateView

from vocabulary.models import VocabularyItem

from .forms import QuizAnswerForm
from .models import UserWordStatus
from .services import (
    DuplicateAnswerError,
    QuizTokenError,
    QuizUnavailableError,
    answer_question,
    generate_question,
    question_from_token,
)


def _is_htmx(request):
    return request.headers.get("HX-Request") == "true"


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


class QuizQuestionView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        pool = kwargs["pool"]
        try:
            question = generate_question(user=request.user, pool=pool)
        except QuizUnavailableError as exc:
            context = {"pool": pool, "unavailable_message": str(exc)}
        else:
            context = {
                "pool": pool,
                "question": question,
                "form": QuizAnswerForm(question=question),
            }

        template = (
            "partials/mcq_question.html"
            if _is_htmx(request)
            else "quizzes/practice.html"
        )
        return render(request, template, context)


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

        context = {"result": result, "pool": question.pool}
        template = (
            "partials/mcq_feedback.html"
            if _is_htmx(request)
            else "quizzes/feedback.html"
        )
        return render(request, template, context)
