from django import forms


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
            (option.vocabulary_item_id, f"{option.label}. {option.definition}")
            for option in question.options
        ]
        self.fields["question_token"].initial = question.token
