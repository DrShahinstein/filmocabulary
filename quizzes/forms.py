from django import forms
from django.utils.text import capfirst

from movies.models import Movie


class PracticeSetupForm(forms.Form):
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


class QuizAnswerForm(forms.Form):
    question_token = forms.CharField(widget=forms.HiddenInput)
    selected_option = forms.TypedChoiceField(
        coerce=int,
        widget=forms.RadioSelect,
        label="Choose the best definition",
    )

    def __init__(self, *args, question, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["selected_option"].choices = [
            (
                option.vocabulary_item_id,
                f"{option.label}) {capfirst(option.definition.strip())}",
            )
            for option in question.options
        ]
        self.fields["question_token"].initial = question.token
