from django.urls import path

from .views import (
    LearningPoolView,
    ProgressDashboardView,
    QuizAnswerView,
    QuizQuestionView,
)

app_name = "quizzes"

urlpatterns = [
    path("", ProgressDashboardView.as_view(), name="dashboard"),
    path("learning/", LearningPoolView.as_view(), name="learning_pool"),
    path("practice/<str:pool>/", QuizQuestionView.as_view(), name="question"),
    path("practice/<str:pool>/answer/", QuizAnswerView.as_view(), name="answer"),
]
