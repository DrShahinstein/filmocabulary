from django.urls import path

from .views import (
    LearningPoolView,
    ProgressDashboardView,
    QuizAnswerView,
    QuizQuestionView,
    QuizSkipView,
    SavedWordsView,
    SavedWordToggleView,
)

app_name = "quizzes"

urlpatterns = [
    path("", ProgressDashboardView.as_view(), name="dashboard"),
    path("learning/", LearningPoolView.as_view(), name="learning_pool"),
    path("saved/", SavedWordsView.as_view(), name="saved_words"),
    path("practice/<str:pool>/", QuizQuestionView.as_view(), name="question"),
    path("practice/<str:pool>/answer/", QuizAnswerView.as_view(), name="answer"),
    path("practice/<str:pool>/skip/", QuizSkipView.as_view(), name="skip"),
    path(
        "words/<int:item_id>/saved/",
        SavedWordToggleView.as_view(),
        name="toggle_saved",
    ),
]
