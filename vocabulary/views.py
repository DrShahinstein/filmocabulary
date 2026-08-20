from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.db.models import CharField, Exists, OuterRef, Q, Subquery, Value
from django.db.models.functions import Coalesce, Lower
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, render
from django.views.decorators.http import require_POST
from django.views.generic import ListView, TemplateView
from django_ratelimit.decorators import ratelimit

from movies.models import Movie
from quizzes.models import UserWordStatus

from .forms import GenerateVocabularyForm, VocabularyExplorerFilterForm
from .models import VocabularyItem
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

    def get_queryset(self):
        status_for_user = UserWordStatus.objects.filter(
            user=self.request.user,
            vocabulary_item_id=OuterRef("pk"),
        ).order_by()
        queryset = (
            VocabularyItem.objects.filter(movie__user=self.request.user)
            .select_related("movie")
            .annotate(
                learning_status=Coalesce(
                    Subquery(status_for_user.values("status")[:1]),
                    Value(UserWordStatus.Status.NEW),
                    output_field=CharField(),
                ),
                is_saved_for_user=Exists(status_for_user.filter(is_saved=True)),
            )
        )

        form = self.get_filter_form()
        if not form.is_valid():
            return queryset.none()

        query = form.cleaned_data["q"]
        if query:
            queryset = queryset.filter(
                Q(word_or_phrase__icontains=query)
                | Q(definition_en__icontains=query)
                | Q(example_sentence__icontains=query)
            )

        status = form.cleaned_data["status"]
        if status == "saved":
            queryset = queryset.filter(is_saved_for_user=True)
        elif status:
            queryset = queryset.filter(learning_status=status)

        if word_type := form.cleaned_data["type"]:
            queryset = queryset.filter(type=word_type)
        if movie := form.cleaned_data["movie"]:
            queryset = queryset.filter(movie=movie)
        if cefr_levels := form.cleaned_data["cefr"]:
            queryset = queryset.filter(cefr_level__in=cefr_levels)

        return queryset.order_by(Lower("word_or_phrase"), "movie__title", "pk")

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["filter_form"] = self.get_filter_form()
        query_parameters = self.request.GET.copy()
        query_parameters.pop("page", None)
        context["filter_query"] = query_parameters.urlencode()
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
