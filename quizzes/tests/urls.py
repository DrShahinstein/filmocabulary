from django.urls import include, path


urlpatterns = [
    path("accounts/", include("django.contrib.auth.urls")),
    path("quizzes/", include("quizzes.urls")),
    path("", include("movies.urls")),
]
