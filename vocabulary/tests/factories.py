def vocabulary_payload(
    *,
    movie_title="Zodiac",
    word="scrutinize",
    item_count=1,
    include_blank=False,
):
    items = []
    for index in range(item_count):
        item_word = word if index == 0 else f"{word}-{index + 1}"
        item = {
            "word_or_phrase": item_word,
            "type": "verb",
            "CEFR_level": "C1",
            "definition_en": "To examine something very carefully.",
            "example_sentence": (
                f"The reporter chose to {item_word} every small detail."
            ),
        }
        if include_blank:
            item["blank_sentence"] = "The reporter chose to ___ every small detail."
        items.append(item)

    return {
        "movie_title": movie_title,
        "items": items,
    }


def vocabulary_item_fields(*, movie, word="scrutinize"):
    return {
        "movie": movie,
        "word_or_phrase": word,
        "type": "verb",
        "cefr_level": "C1",
        "definition_en": "To examine something very carefully.",
        "example_sentence": f"The reporter chose to {word} every small detail.",
        "blank_sentence": "The reporter chose to ___ every small detail.",
    }
