from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models.functions import Lower
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.urls import reverse
from django.views.decorators.http import require_POST
from django.views.generic import ListView, TemplateView
from django_ratelimit.decorators import ratelimit

from movies.models import Movie
from quizzes.services import (
    TARGETED_POOL,
    sign_targeted_scope,
    targeted_practice_availability,
)

from .forms import GenerateVocabularyForm, VocabularyExplorerFilterForm
from .models import VocabularyItem
from .querysets import (
    VocabularyFilterSpec,
    filter_vocabulary_queryset,
    owned_vocabulary_queryset,
)
from .services import (
    VocabularyGenerationResult,
    VocabularyGenerationError,
    VocabularyYieldReason,
    generate_and_save_vocabulary,
    prepare_vocabulary_source,
)


GENERATION_RATE = getattr(settings, "VOCABULARY_GENERATION_RATE", "5/h")


def _is_htmx(request: HttpRequest) -> bool:
    return request.headers.get("HX-Request") == "true"


def _counted(count: int, singular: str, plural: str | None = None) -> str:
    noun = singular if count == 1 else (plural or f"{singular}s")
    return f"{count} {noun}"


def _yield_message(result: VocabularyGenerationResult) -> str | None:
    if not result.has_shortfall or result.requested_count is None:
        return None

    sentences = [
        f"Saved {result.created_count} of {result.requested_count} requested new entries."
    ]
    reasons = set(result.yield_reasons)
    if VocabularyYieldReason.PROVIDER_SHORTFALL in reasons:
        sentences.append("This generation run returned fewer candidates than requested.")
    if VocabularyYieldReason.GENERATED_DUPLICATE in reasons:
        count = result.candidate_rejections.duplicate
        sentences.append(
            f"{_counted(count, 'generated candidate')} repeated another term and "
            f"{'was' if count == 1 else 'were'} excluded."
        )
    if VocabularyYieldReason.UNGROUNDED in reasons:
        count = result.candidate_rejections.ungrounded
        sentences.append(
            f"{_counted(count, 'generated candidate')} did not match the supplied source and "
            f"{'was' if count == 1 else 'were'} excluded."
        )
    if VocabularyYieldReason.INVALID_SCHEMA in reasons:
        count = result.candidate_rejections.malformed
        sentences.append(
            f"{_counted(count, 'generated candidate')} had invalid structured data and "
            f"{'was' if count == 1 else 'were'} excluded."
        )
    if VocabularyYieldReason.ALREADY_SAVED in reasons:
        sentences.append(
            f"{_counted(result.skipped_count, 'qualifying entry', 'qualifying entries')} "
            f"{'was' if result.skipped_count == 1 else 'were'} already saved and "
            f"{'was' if result.skipped_count == 1 else 'were'} not duplicated."
        )
    if VocabularyYieldReason.OTHER in reasons:
        sentences.append("This run produced fewer new qualifying entries than requested.")
    sentences.append("Every qualifying entry from this run was saved.")
    return " ".join(sentences)


class VocabularyListView(LoginRequiredMixin, TemplateView):
    template_name = "partials/vocabulary_list.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["movies"] = Movie.objects.filter(user=self.request.user).prefetch_related(
            "vocabulary_items"
        )
        context["form"] = GenerateVocabularyForm()
        return context


class VocabularyExplorerView(LoginRequiredMixin, ListView):
    context_object_name = "items"
    paginate_by = 24

    def get_template_names(self):
        if _is_htmx(self.request):
            return ("partials/word_explorer_results.html",)
        return ("vocabulary/words_explorer.html",)

    def get_filter_form(self):
        if not hasattr(self, "filter_form"):
            self.filter_form = VocabularyExplorerFilterForm(
                self.request.GET,
                user=self.request.user,
            )
        return self.filter_form

    def get_filter_spec(self):
        if not hasattr(self, "filter_spec"):
            form = self.get_filter_form()
            self.filter_spec = (
                VocabularyFilterSpec.from_cleaned_data(form.cleaned_data)
                if form.is_valid()
                else None
            )
        return self.filter_spec

    def get_queryset(self):
        queryset = owned_vocabulary_queryset(self.request.user)
        filter_spec = self.get_filter_spec()
        if filter_spec is None:
            return queryset.none()

        return filter_vocabulary_queryset(queryset, filter_spec).order_by(
            Lower("word_or_phrase"), "movie__title", "pk"
        )

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter_form"] = self.get_filter_form()
        filter_spec = self.get_filter_spec()
        context["vocabulary_filter_spec"] = filter_spec
        context["filter_query"] = (
            filter_spec.as_query_string() if filter_spec is not None else ""
        )
        context["definition_quiz_available"] = False
        context["cloze_quiz_available"] = False
        context["targeted_definition_url"] = ""
        context["targeted_cloze_url"] = ""
        if filter_spec is not None:
            definition_available, cloze_available = targeted_practice_availability(
                user=self.request.user,
                filter_spec=filter_spec,
            )
            context["definition_quiz_available"] = definition_available
            context["cloze_quiz_available"] = cloze_available
            if definition_available or cloze_available:
                scope_token = sign_targeted_scope(
                    user=self.request.user,
                    filter_spec=filter_spec,
                )
                practice_url = reverse("quizzes:question", args=[TARGETED_POOL])
                if definition_available:
                    context["targeted_definition_url"] = (
                        f"{practice_url}?mode=definition&scope={scope_token}"
                    )
                if cloze_available:
                    context["targeted_cloze_url"] = (
                        f"{practice_url}?mode=cloze&scope={scope_token}"
                    )
        return context


class MovieVocabularyDetailView(LoginRequiredMixin, TemplateView):
    def get_template_names(self):
        if self.request.headers.get("HX-Request") == "true":
            return ("partials/vocabulary_movie_items.html",)
        return ("vocabulary/movie_detail.html",)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        movie = get_object_or_404(
            Movie,
            pk=self.kwargs["movie_pk"],
            user=self.request.user,
        )
        context["movie"] = movie
        context["items"] = owned_vocabulary_queryset(self.request.user).filter(
            movie=movie
        )
        context["show_bookmarks"] = True
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
            "yield_message": _yield_message(result),
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

    item.delete()

    response = HttpResponse(status=204)
    response["HX-Trigger"] = "vocabularyChanged"
    return response
