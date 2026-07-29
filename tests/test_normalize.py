"""Normalization is the dedup key space; these pin its exact rules."""

import pytest

from api.dedup.normalize import normalize_doi, normalize_title


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://doi.org/10.1093/JAMIA/ocz200", "10.1093/jamia/ocz200"),
        ("http://doi.org/10.5555/X", "10.5555/x"),
        ("doi:10.1000/ABC", "10.1000/abc"),
        ("DOI:10.1000/abc", "10.1000/abc"),
        ("  10.1000/abc  ", "10.1000/abc"),
        ("", None),
        (None, None),
    ],
)
def test_normalize_doi(raw: str | None, expected: str | None) -> None:
    assert normalize_doi(raw) == expected


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Attention Is All You Need", "attention is all you need"),
        ("Attention is all you need.", "attention is all you need"),
        ("The BERT   Model:  A Survey!", "bert model a survey"),
        ("BioBART: pre-training", "biobart pre training"),
        # A title that IS just "The" must not normalize to empty.
        ("The", "the"),
    ],
)
def test_normalize_title(raw: str, expected: str) -> None:
    assert normalize_title(raw) == expected


def test_normalized_variants_collide() -> None:
    # The property the dedup cascade relies on: cross-source variants of one
    # paper produce one key.
    variants = [
        "Attention Is All You Need",
        "Attention is all you need",
        "Attention is all you need.",
    ]
    assert len({normalize_title(v) for v in variants}) == 1
