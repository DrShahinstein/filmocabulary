from django.urls import path

from .views import VocabularyExplorerView


app_name = "words"

urlpatterns = [
    path("", VocabularyExplorerView.as_view(), name="index"),
]
