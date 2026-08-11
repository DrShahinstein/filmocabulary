from django import forms

from movies.models import Movie
from vocabulary.models import VocabularyItem

from .services import MAX_QUESTIONS, eligible_questions


class QuizStartForm(forms.Form):
    movies = forms.ModelMultipleChoiceField(
        queryset=Movie.objects.none(),
        widget=forms.CheckboxSelectMultiple,
        label="Movies",
        help_text="Choose one or more movies.",
    )
    question_count = forms.IntegerField(
        min_value=1,
        max_value=MAX_QUESTIONS,
        initial=10,
        label="Questions",
        widget=forms.NumberInput(attrs={"inputmode": "numeric"}),
    )

    def __init__(self, *args, user, **kwargs):
        super().__init__(*args, **kwargs)
        self.user = user
        self.fields["movies"].queryset = Movie.objects.filter(user=user).order_by(
            "title", "release_year"
        )

    def clean(self):
        cleaned_data = super().clean()
        movies = cleaned_data.get("movies")
        question_count = cleaned_data.get("question_count")
        if not movies or question_count is None:
            return cleaned_data

        available_count = eligible_questions(movies).count()
        if available_count == 0:
            self.add_error(
                "movies",
                "The selected movies do not have any quiz-ready vocabulary yet.",
            )
        elif question_count > available_count:
            self.add_error(
                "question_count",
                f"Choose {available_count} questions or fewer for these movies.",
            )
        return cleaned_data


class QuizAnswerForm(forms.Form):
    vocabulary_item = forms.ModelChoiceField(
        queryset=VocabularyItem.objects.none(),
        widget=forms.HiddenInput,
    )
    submitted_answer = forms.CharField(
        max_length=255,
        label="Your answer",
        strip=True,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "autocapitalize": "none",
                "spellcheck": "false",
                "placeholder": "Type the missing word or phrase",
                "autofocus": "autofocus",
                "data-autofocus": "true",
            }
        ),
    )

    def __init__(self, *args, session, **kwargs):
        super().__init__(*args, **kwargs)
        self.session = session
        self.fields["vocabulary_item"].queryset = session.questions.all()
