from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.views.decorators.http import require_POST
from django.views.generic import TemplateView

from .models import Movie


class DashboardView(LoginRequiredMixin, TemplateView):
    template_name = "movies/dashboard.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["movies"] = self.request.user.movies.prefetch_related(
            "vocabulary_items"
        )

        from vocabulary.forms import VocabularyGenerationForm

        context["generation_form"] = VocabularyGenerationForm()
        return context


@login_required
@require_POST
def delete_movie(request: HttpRequest, pk: int) -> HttpResponse:
    movie = get_object_or_404(Movie, pk=pk, user=request.user)
    movie_title = movie.title

    movie.delete()

    if request.headers.get("HX-Request") == "true":
        response = render(
            request,
            "partials/movie_library.html",
            {
                "movies": request.user.movies.prefetch_related(
                    "vocabulary_items"
                )
            },
        )
        response["HX-Trigger-After-Swap"] = "vocabularyChanged, movieDeleted"
        return response

    messages.success(request, f'"{movie_title}" was deleted from your library.')
    return redirect("movies:dashboard")
