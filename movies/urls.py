from django.urls import path

from .views import DashboardView, delete_movie

app_name = "movies"

urlpatterns = [
    path("", DashboardView.as_view(), name="dashboard"),
    path("movies/<int:pk>/delete/", delete_movie, name="delete"),
]
