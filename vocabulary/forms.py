from django import forms
from django.conf import settings
from django.db.models.functions import Lower

from movies.models import Movie, current_year

from .constants import MAX_GENERATION_ITEMS
from .ingestion import SourceDocument, SourceIngestionError, parse_uploaded_source
from .models import VocabularyItem


class VocabularyExplorerFilterForm(forms.Form):
    STATUS_CHOICES = (
        ("", "All statuses"),
        ("new", "New"),
        ("learning", "Learning"),
        ("mastered", "Mastered"),
        ("saved", "Saved"),
    )

    q = forms.CharField(
        required=False,
        max_length=255,
        label="Search",
        widget=forms.SearchInput(
            attrs={
                "autocomplete": "off",
                "placeholder": "Search words and definitions",
            }
        ),
    )
    status = forms.ChoiceField(
        required=False,
        choices=STATUS_CHOICES,
        label="Status",
    )
    type = forms.ChoiceField(
        required=False,
        choices=(("", "All types"), *VocabularyItem.Type.choices),
        label="Type",
    )
    movie = forms.ModelChoiceField(
        required=False,
        queryset=Movie.objects.none(),
        empty_label="All movies",
        label="Movie",
    )
    cefr = forms.MultipleChoiceField(
        required=False,
        choices=VocabularyItem.CefrLevel.choices,
        widget=forms.CheckboxSelectMultiple,
        label="CEFR level",
    )

    def __init__(self, *args, user=None, **kwargs):
        super().__init__(*args, **kwargs)
        if user is not None:
            self.fields["movie"].queryset = Movie.objects.filter(user=user).order_by(
                Lower("title"), "pk"
            )

    def clean_q(self):
        return " ".join(self.cleaned_data["q"].split())


class VocabularyGenerationForm(forms.Form):
    title = forms.CharField(
        label="Movie title",
        max_length=255,
        widget=forms.TextInput(
            attrs={
                "autocomplete": "off",
                "placeholder": "The Matrix",
                "autofocus": True,
            }
        ),
    )
    release_year = forms.IntegerField(
        required=False,
        min_value=1888,
        max_value=current_year(),
        widget=forms.NumberInput(attrs={"placeholder": "1999"}),
    )
    item_count = forms.IntegerField(
        min_value=1,
        max_value=MAX_GENERATION_ITEMS,
        initial=getattr(settings, "VOCABULARY_DEFAULT_ITEM_COUNT", 12),
        label="Entries",
    )
    source_file = forms.FileField(
        required=False,
        label="Script or subtitles (optional)",
        widget=forms.ClearableFileInput(
            attrs={
                "accept": ".txt,.srt,text/plain,application/x-subrip",
            }
        ),
    )

    source_document: SourceDocument | None = None

    def clean_title(self) -> str:
        return " ".join(self.cleaned_data["title"].split())

    def clean_source_file(self):
        uploaded_file = self.cleaned_data.get("source_file")
        if uploaded_file is None:
            self.source_document = None
            return None
        try:
            self.source_document = parse_uploaded_source(uploaded_file)
        except SourceIngestionError as exc:
            raise forms.ValidationError(str(exc)) from exc
        return uploaded_file


# Kept as a compatibility alias for app-local callers.
GenerateVocabularyForm = VocabularyGenerationForm
