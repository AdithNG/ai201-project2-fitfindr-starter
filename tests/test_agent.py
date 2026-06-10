"""
tests/test_agent.py

Planning-loop / error-handling tests. These verify the agent's behavior changes
based on what the tools return — the no-results early exit, the size-retry
branch, the empty-query guard, and that state flows between tools.

Run with:  pytest tests/
"""

from agent import run_agent, _parse_query
from utils.data_loader import get_example_wardrobe, get_empty_wardrobe


# -- query parsing -------------------------------------------------------------

def test_parse_extracts_price_and_size():
    parsed = _parse_query("vintage graphic tee size M under $30")
    assert parsed["max_price"] == 30.0
    assert parsed["size"] == "M"
    assert "tee" in parsed["description"]
    # consumed phrases are stripped from the description
    assert "$30" not in parsed["description"]
    assert "size M" not in parsed["description"]


# -- happy path: all three tools run, state flows ------------------------------

def test_happy_path_runs_all_tools():
    s = run_agent("vintage graphic tee under $30", get_example_wardrobe())
    assert s["error"] is None
    assert s["selected_item"] is not None
    # the selected item is the top search result (state, not re-entry)
    assert s["selected_item"] is s["search_results"][0]
    assert s["outfit_suggestion"] and s["fit_card"]


# -- no-results early exit: styling tools must NOT run -------------------------

def test_no_results_sets_error_and_stops():
    s = run_agent("designer ballgown size XXS under $5", get_example_wardrobe())
    assert s["error"]                      # user-facing message set
    assert s["outfit_suggestion"] is None  # suggest_outfit never called
    assert s["fit_card"] is None           # create_fit_card never called


# -- retry branch: an unmatchable size loosens the filter and notes it ---------

def test_size_retry_branch():
    s = run_agent("flowy midi skirt size XXL under $40", get_example_wardrobe())
    # a size that matches nothing should trigger the drop-size retry
    assert s["error"] is None
    assert s["selected_item"] is not None
    assert s["adjustment_note"] is not None


# -- empty query guard ---------------------------------------------------------

def test_empty_query_guarded():
    s = run_agent("   ", get_example_wardrobe())
    assert s["error"]
    assert s["fit_card"] is None


# -- empty wardrobe still completes --------------------------------------------

def test_empty_wardrobe_still_produces_card():
    s = run_agent("vintage graphic tee under $30", get_empty_wardrobe())
    assert s["error"] is None
    assert s["outfit_suggestion"] and s["fit_card"]
