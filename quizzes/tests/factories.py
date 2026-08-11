from django.contrib.auth import get_user_model

from movies.models import Movie
from vocabulary.models import VocabularyItem


def make_user(username):
    return get_user_model().objects.create_user(
        username=username,
        email=f"{username}@example.com",
        password="test-password",
    )


def make_movie(user, title="Arrival", release_year=2016):
    return Movie.objects.create(
        user=user,
        title=title,
        release_year=release_year,
    )


def make_vocabulary(movie, word="meticulous", sentence=None):
    sentence = sentence or f"Her ___ notes made the pattern clear."
    return VocabularyItem.objects.create(
        movie=movie,
        word_or_phrase=word,
        type="adjective",
        cefr_level="C1",
        definition_en="Showing careful attention to detail.",
        example_sentence=sentence.replace("___", word),
        blank_sentence=sentence,
    )
