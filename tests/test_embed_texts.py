"""Pins the bge asymmetric-prefix contract (DECISION-2b, Kishan's
requirement 1): these tests FAIL if the query path loses its prefix or the
document path grows one — the failure mode is silent retrieval degradation,
so a loud test is the only tripwire."""

from api.embed.texts import QUERY_PREFIX, document_text, query_text


def test_query_text_carries_the_exact_bge_instruction_prefix() -> None:
    # The exact model-card string, trailing space included: a paraphrase or
    # a lost space is a different string to the tokenizer.
    assert QUERY_PREFIX == "Represent this sentence for searching relevant passages: "
    assert query_text("clinical text simplification") == (
        "Represent this sentence for searching relevant passages: clinical text simplification"
    )


def test_document_text_never_carries_the_prefix() -> None:
    doc = document_text("A title", "An abstract about EHR notes.")
    assert doc == "A title. An abstract about EHR notes."
    assert QUERY_PREFIX not in doc
    # Even a hostile title that MENTIONS the prefix text must not put the
    # document on the query side of the space at position zero.
    weird = document_text("Represent this sentence for searching relevant passages", None)
    assert not weird.startswith(QUERY_PREFIX)


def test_document_text_degrades_to_title_only() -> None:
    assert document_text("Just a title", None) == "Just a title"
    assert document_text("Just a title", "") == "Just a title"


def test_query_and_document_paths_are_asymmetric() -> None:
    """The actual invariant: identical input text produces different encoder
    input depending on which side it enters — queries prefixed, documents
    not. If someone 'simplifies' the two functions into one, this fails."""
    text = "text simplification for aphasia"
    assert query_text(text) != document_text(text, None)
    assert query_text(text).endswith(document_text(text, None))
    assert query_text(text).startswith(QUERY_PREFIX)
