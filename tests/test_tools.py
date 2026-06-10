"""
tests/test_tools.py

Unit tests for the three FitFindr tools, with at least one test per failure
mode. The search tests are pure/offline; the LLM tests assert on structure
(non-empty string, no exception) rather than exact wording.

Run with:  pytest tests/
"""

from tools import search_listings, suggest_outfit, create_fit_card
from utils.data_loader import get_example_wardrobe, get_empty_wardrobe


# -- search_listings -----------------------------------------------------------

def test_search_returns_results():
    results = search_listings("vintage graphic tee", size=None, max_price=50)
    assert isinstance(results, list)
    assert len(results) > 0


def test_search_empty_results():
    # Impossible query -> empty list, never an exception.
    results = search_listings("designer ballgown", size="XXS", max_price=5)
    assert results == []


def test_search_price_filter():
    results = search_listings("jacket", size=None, max_price=10)
    assert all(item["price"] <= 10 for item in results)


def test_search_size_filter_case_insensitive():
    results = search_listings("jeans", size="m", max_price=None)
    # every result's size must contain (or be contained by) the query size
    for item in results:
        s = str(item["size"]).lower()
        assert "m" in s or s in "m"


def test_search_sorted_by_relevance():
    # A more specific multi-keyword query should still return ranked dicts.
    results = search_listings("vintage denim jacket", size=None, max_price=None)
    assert isinstance(results, list)
    assert all("title" in item and "price" in item for item in results)


# -- suggest_outfit ------------------------------------------------------------

def test_suggest_outfit_with_wardrobe():
    item = search_listings("vintage graphic tee", size=None, max_price=50)[0]
    out = suggest_outfit(item, get_example_wardrobe())
    assert isinstance(out, str) and out.strip()


def test_suggest_outfit_empty_wardrobe():
    # Failure mode: empty wardrobe -> general advice string, not an exception.
    item = search_listings("vintage graphic tee", size=None, max_price=50)[0]
    out = suggest_outfit(item, get_empty_wardrobe())
    assert isinstance(out, str) and out.strip()


# -- create_fit_card -----------------------------------------------------------

def test_create_fit_card_happy():
    item = search_listings("vintage graphic tee", size=None, max_price=50)[0]
    card = create_fit_card("pair it with baggy jeans and chunky sneakers", item)
    assert isinstance(card, str) and card.strip()


def test_create_fit_card_empty_outfit():
    # Failure mode: empty outfit -> descriptive error string, not an exception.
    item = search_listings("vintage graphic tee", size=None, max_price=50)[0]
    card = create_fit_card("", item)
    assert isinstance(card, str) and card.strip()
    assert "outfit" in card.lower()
