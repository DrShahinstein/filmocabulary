from django import forms
from django.utils.text import capfirst

from movies.models import Movie

from .services import CLOZE_MODE, DEFINITION_MODE, MIXED_MODE


QUIZ_MODE_CHOICES = (
    (DEFINITION_MODE, "Definition only"),
    (CLOZE_MODE, "Fill-in-the-blanks only"),
    (MIXED_MODE, "Mixed"),
)


class PracticeSetupForm(forms.Form):
    mode = forms.ChoiceField(
        choices=QUIZ_MODE_CHOICES,
        required=False,
        initial=MIXED_MODE,
        widget=forms.RadioSelect,
        label="Quiz mode",
        help_text=(
            "Mixed practice randomly alternates, using cloze only when the selected "
            "word supports it."
        ),
    )
    movies = forms.ModelMultipleChoiceField(
        queryset=Movie.objects.none(),
        required=False,
        widget=forms.CheckboxSelectMultiple,
        label="Choose movies",
        help_text="Select one or more movies, or leave all unchecked to use your full collection.",
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["movies"].queryset = (
            Movie.objects.filter(
                user=user,
                vocabulary_items__isnull=False,
            )
            .distinct()
            .order_by("title", "release_year", "pk")
        )

    def clean_mode(self):
        # Existing practice links without a mode remain definition quizzes.
        return self.cleaned_data["mode"] or DEFINITION_MODE


class QuizAnswerForm(forms.Form):
    question_token = forms.CharField(widget=forms.HiddenInput)
    selected_option = forms.TypedChoiceField(
        coerce=int,
        widget=forms.RadioSelect,
        label="Choose the best definition",
    )

    def __init__(self, *args, question, **kwargs):
        super().__init__(*args, **kwargs)
        is_cloze = question.kind == CLOZE_MODE
        if is_cloze:
            self.fields["selected_option"].label = "Choose the missing word or phrase"

        def option_text(option):
            if is_cloze:
                return option.word_or_phrase.strip()
            return capfirst(option.definition.strip())

        self.fields["selected_option"].choices = [
            (
                option.vocabulary_item_id,
                f"{option.label}) {option_text(option)}",
            )
            for option in question.options
        ]
        self.fields["question_token"].initial = question.token


class QuizContinuationForm(forms.Form):
    token = forms.CharField(max_length=8192)


class TargetedPracticeLaunchForm(forms.Form):
    mode = forms.ChoiceField(
        choices=QUIZ_MODE_CHOICES[:2],
    )
    scope = forms.CharField(max_length=2048)
