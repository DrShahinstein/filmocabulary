from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.exceptions import ValidationError
from django.http import HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.views import View
from django.views.generic import FormView, ListView, TemplateView

from .forms import QuizAnswerForm, QuizStartForm
from .models import QuizSession
from .services import create_quiz_session, next_unanswered_question, record_answer


def _is_htmx(request):
    return request.headers.get("HX-Request") == "true"


def _owned_session(user, pk):
    return get_object_or_404(
        QuizSession.objects.prefetch_related("selected_movies", "questions"),
        pk=pk,
        user=user,
    )


def _play_context(session, *, form=None):
    question = None if session.completed_at else next_unanswered_question(session)
    answered_count = session.attempts.count()
    total = session.total_questions
    if form is None and question is not None:
        form = QuizAnswerForm(
            session=session,
            initial={"vocabulary_item": question},
        )
    return {
        "session": session,
        "question": question,
        "form": form,
        "answered_count": answered_count,
        "question_number": min(answered_count + 1, total),
        "progress_percent": round((answered_count / total) * 100) if total else 0,
        "is_complete": question is None,
    }


class QuizStartView(LoginRequiredMixin, FormView):
    template_name = "quizzes/start.html"
    form_class = QuizStartForm

    def get_form_kwargs(self):
        kwargs = super().get_form_kwargs()
        kwargs["user"] = self.request.user
        return kwargs

    def form_valid(self, form):
        try:
            session = create_quiz_session(
                user=self.request.user,
                movies=form.cleaned_data["movies"],
                question_count=form.cleaned_data["question_count"],
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return self.form_invalid(form)

        play_url = reverse("quizzes:play", kwargs={"pk": session.pk})
        if not _is_htmx(self.request):
            return redirect(play_url)

        response = render(
            self.request,
            "partials/quiz_question.html",
            _play_context(session),
        )
        response["HX-Push-Url"] = play_url
        return response

    def form_invalid(self, form):
        if _is_htmx(self.request):
            return render(
                self.request,
                "partials/quiz_start_form.html",
                {"form": form},
            )
        return super().form_invalid(form)


class QuizBuilderView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        return render(
            request,
            "partials/quiz_start_form.html",
            {"form": QuizStartForm(user=request.user)},
        )


class QuizPlayView(LoginRequiredMixin, TemplateView):
    template_name = "quizzes/play.html"

    def get(self, request, *args, **kwargs):
        session = _owned_session(request.user, kwargs["pk"])
        context = _play_context(session)
        if _is_htmx(request):
            template = (
                "partials/quiz_complete.html"
                if context["is_complete"]
                else "partials/quiz_question.html"
            )
            return render(request, template, context)
        return render(request, self.template_name, context)


class QuizAnswerView(LoginRequiredMixin, View):
    def get(self, request, *args, **kwargs):
        return HttpResponseNotAllowed(["POST"])

    def post(self, request, *args, **kwargs):
        session = _owned_session(request.user, kwargs["pk"])
        form = QuizAnswerForm(request.POST, session=session)
        if not form.is_valid():
            return render(
                request,
                "partials/quiz_question.html",
                _play_context(session, form=form),
            )

        try:
            result = record_answer(
                session=session,
                vocabulary_item=form.cleaned_data["vocabulary_item"],
                submitted_answer=form.cleaned_data["submitted_answer"],
            )
        except ValidationError as exc:
            form.add_error(None, exc)
            return render(
                request,
                "partials/quiz_question.html",
                _play_context(session, form=form),
            )

        context = {
            "session": result.session,
            "attempt": result.attempt,
            "was_duplicate": not result.created,
            "answered_count": result.answered_count,
            "progress_percent": round(
                (result.answered_count / result.session.total_questions) * 100
            ),
            "is_complete": result.is_complete,
        }
        template = (
            "partials/quiz_feedback.html"
            if _is_htmx(request)
            else "quizzes/feedback.html"
        )
        return render(request, template, context)


class QuizHistoryView(LoginRequiredMixin, ListView):
    template_name = "quizzes/history.html"
    context_object_name = "sessions"
    paginate_by = 20

    def get_queryset(self):
        return (
            QuizSession.objects.filter(user=self.request.user)
            .prefetch_related("selected_movies")
            .order_by("-created_at")
        )
