from django.urls import path

from . import views


app_name = "vocabulary"

urlpatterns = [
    path("", views.VocabularyListView.as_view(), name="list"),
    path("generate/", views.generate_vocabulary, name="generate"),
    path(
        "movies/<int:movie_pk>/",
        views.MovieVocabularyDetailView.as_view(),
        name="movie_detail",
    ),
    path(
        "items/<int:pk>/delete/",
        views.delete_vocabulary_item,
        name="item_delete",
    ),
]

