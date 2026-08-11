from django.urls import path

from .views import (
    QuizAnswerView,
    QuizBuilderView,
    QuizHistoryView,
    QuizPlayView,
    QuizStartView,
)

app_name = "quizzes"

urlpatterns = [
    path("", QuizStartView.as_view(), name="start"),
    path("builder/", QuizBuilderView.as_view(), name="builder"),
    path("history/", QuizHistoryView.as_view(), name="history"),
    path("<int:pk>/", QuizPlayView.as_view(), name="play"),
    path("<int:pk>/answer/", QuizAnswerView.as_view(), name="answer"),
]
