from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db import transaction
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView
from django_ratelimit.decorators import ratelimit

from movies.models import Movie

from .forms import GenerateVocabularyForm
from .models import VocabularyItem
from .services import (
    VocabularyGenerationError,
    generate_and_save_vocabulary,
    prepare_vocabulary_source,
)


GENERATION_RATE = getattr(settings, "VOCABULARY_GENERATION_RATE", "5/h")


class VocabularyListView(LoginRequiredMixin, TemplateView):
    template_name = "partials/vocabulary_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["movies"] = Movie.objects.filter(user=self.request.user).prefetch_related(
            "vocabulary_items"
        )
        context["form"] = GenerateVocabularyForm()
        return context


class MovieVocabularyDetailView(LoginRequiredMixin, TemplateView):
    def get_template_names(self):
        if self.request.headers.get("HX-Request") == "true":
            return ("partials/vocabulary_movie_items.html",)
        return ("vocabulary/movie_detail.html",)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        movie = get_object_or_404(
            Movie.objects.prefetch_related("vocabulary_items"),
            pk=self.kwargs["movie_pk"],
            user=self.request.user,
        )
        context["movie"] = movie
        context["items"] = movie.vocabulary_items.all()
        return context


@login_required
@require_POST
@ratelimit(key="user", rate=GENERATION_RATE, method="POST", block=False)
def generate_vocabulary(request: HttpRequest) -> HttpResponse:
    if getattr(request, "limited", False):
        return render(
            request,
            "partials/vocabulary_error.html",
            {
                "message": (
                    "You have reached the vocabulary generation limit. "
                    "Please try again later."
                )
            },
            status=429,
        )

    form = GenerateVocabularyForm(request.POST, request.FILES)
    if not form.is_valid():
        return render(
            request,
            "partials/vocabulary_error.html",
            {
                "message": "Please correct the vocabulary request.",
                "form": form,
            },
            status=422,
        )

    prepared_source = prepare_vocabulary_source(
        user=request.user,
        title=form.cleaned_data["title"],
        release_year=form.cleaned_data["release_year"],
        item_count=form.cleaned_data["item_count"],
        uploaded_source=form.source_document,
    )

    generation_arguments = {
        "item_count": form.cleaned_data["item_count"],
        "source": prepared_source.source,
    }
    if prepared_source.movie is not None:
        generation_arguments["movie"] = prepared_source.movie
    else:
        generation_arguments.update(
            {
                "user": request.user,
                "title": form.cleaned_data["title"],
                "release_year": form.cleaned_data["release_year"],
            }
        )

    try:
        result = generate_and_save_vocabulary(**generation_arguments)
    except VocabularyGenerationError as exc:
        return render(
            request,
            "partials/vocabulary_error.html",
            {"message": str(exc)},
            status=503,
        )

    movie = result.movie
    items = movie.vocabulary_items.all()
    response = render(
        request,
        "partials/vocabulary_generation_success.html",
        {
            "movie": movie,
            "items": items,
            "created_count": result.created_count,
            "skipped_count": result.skipped_count,
            "source_note": prepared_source.note,
            "movies": Movie.objects.filter(user=request.user).prefetch_related(
                "vocabulary_items"
            ),
        },
    )
    response["HX-Trigger"] = "vocabularyChanged"
    return response


@login_required
@require_POST
def delete_vocabulary_item(request: HttpRequest, pk: int) -> HttpResponse:
    item = get_object_or_404(
        VocabularyItem.objects.select_related("movie"),
        pk=pk,
        movie__user=request.user,
    )

    with transaction.atomic():
        # A quiz stores fixed totals and scores, so removing one of its questions
        # would make both active and completed sessions internally inconsistent.
        from quizzes.models import QuizSession

        QuizSession.objects.filter(questions=item).delete()
        item.delete()

    response = HttpResponse(status=204)
    response["HX-Trigger"] = "vocabularyChanged"
    return response
