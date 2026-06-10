"""
agent.py

The FitFindr planning loop. Orchestrates the three tools in response to a
natural language user query, passing state between them via a session dict.

Complete tools.py and test each tool in isolation before implementing this file.

Usage (once implemented):
    from agent import run_agent
    from utils.data_loader import get_example_wardrobe

    result = run_agent(
        query="vintage graphic tee under $30, size M",
        wardrobe=get_example_wardrobe(),
    )
    print(result["fit_card"])
    print(result["error"])   # None on success
"""

import re

from tools import search_listings, suggest_outfit, create_fit_card


# ── query parsing ─────────────────────────────────────────────────────────────

def _parse_query(query: str) -> dict:
    """
    Extract a description, optional size, and optional max_price from free text.

    Regex-based (not an LLM call) so parsing is deterministic, free, and testable.
    Recognizes prices like "under $30", "$30", "under 30" and sizes like
    "size M", "size 8". The matched price/size phrases are stripped from the
    leftover text, which becomes the description.
    """
    text = query or ""
    max_price = None
    size = None

    # price: "under $30", "$30", "under 30", "below 25"
    price_match = re.search(
        r"(?:under|below|less than|max|<=?)\s*\$?\s*(\d+(?:\.\d+)?)"
        r"|\$\s*(\d+(?:\.\d+)?)",
        text, flags=re.IGNORECASE,
    )
    if price_match:
        max_price = float(price_match.group(1) or price_match.group(2))

    # size: "size M", "size 8", "in a size 10"
    size_match = re.search(
        r"\bsize\s+([a-z0-9]{1,4})\b", text, flags=re.IGNORECASE,
    )
    if size_match:
        size = size_match.group(1).upper()

    # description = original text minus the price/size phrases we consumed
    description = text
    if price_match:
        description = description.replace(price_match.group(0), " ")
    if size_match:
        description = description.replace(size_match.group(0), " ")
    description = re.sub(r"\s+", " ", description).strip()

    return {"description": description, "size": size, "max_price": max_price}


# ── session state ─────────────────────────────────────────────────────────────

def _new_session(query: str, wardrobe: dict) -> dict:
    """
    Initialize and return a fresh session dict for one user interaction.

    The session dict is the single source of truth for everything that happens
    during a run — it stores the original query, parsed parameters, tool results,
    and any error that caused early termination.

    You may add fields to this dict as needed for your implementation.
    """
    return {
        "query": query,              # original user query
        "parsed": {},                # extracted description / size / max_price
        "search_results": [],        # list of matching listing dicts
        "adjustment_note": None,     # set if the loop loosened a filter to find results
        "selected_item": None,       # top result, passed into suggest_outfit
        "wardrobe": wardrobe,        # user's wardrobe dict
        "outfit_suggestion": None,   # string returned by suggest_outfit
        "fit_card": None,            # string returned by create_fit_card
        "error": None,               # set if the interaction ended early
    }


# ── planning loop ─────────────────────────────────────────────────────────────

def run_agent(query: str, wardrobe: dict) -> dict:
    """
    Main agent entry point. Runs the FitFindr planning loop for a single
    user interaction and returns the completed session dict.

    Args:
        query:    Natural language user request
                  (e.g., "vintage graphic tee under $30, size M")
        wardrobe: User's wardrobe dict — use get_example_wardrobe() or
                  get_empty_wardrobe() from utils/data_loader.py

    Returns:
        The session dict after the interaction completes. Check session["error"]
        first — if it is not None, the interaction ended early and the other
        output fields (outfit_suggestion, fit_card) will be None.

    TODO — implement this function using the planning loop you designed in planning.md:

        Step 1: Initialize the session with _new_session().

        Step 2: Parse the user's query to extract a description, size, and
                max_price. You can use regex, string splitting, or ask the LLM
                to parse it — document your choice in planning.md.
                Store the result in session["parsed"].

        Step 3: Call search_listings() with the parsed parameters.
                Store results in session["search_results"].
                If no results: set session["error"] to a helpful message and
                return the session early. Do NOT proceed to suggest_outfit
                with empty input.

        Step 4: Select the item to use (e.g., the top result).
                Store it in session["selected_item"].

        Step 5: Call suggest_outfit() with the selected item and wardrobe.
                Store the result in session["outfit_suggestion"].

        Step 6: Call create_fit_card() with the outfit suggestion and selected item.
                Store the result in session["fit_card"].

        Step 7: Return the session.

    Before writing code, complete the Planning Loop and State Management sections
    of planning.md — your implementation should match what you described there.
    """
    session = _new_session(query, wardrobe)

    # Step 1 — parse the query into description / size / max_price.
    session["parsed"] = _parse_query(query)
    parsed = session["parsed"]
    if not parsed["description"]:
        session["error"] = "Tell me what you're looking for — try 'vintage denim jacket under $40'."
        return session

    # Step 2 — search.
    results = search_listings(
        parsed["description"], parsed["size"], parsed["max_price"]
    )
    session["search_results"] = results

    # Step 3 — branch on the search result.
    if not results and parsed["size"]:
        # 3b — retry once with the size filter dropped (common over-constraint).
        results = search_listings(
            parsed["description"], None, parsed["max_price"]
        )
        session["search_results"] = results
        if results:
            session["adjustment_note"] = (
                f"No exact matches in size {parsed['size']}, so I dropped the size "
                "filter and found these instead."
            )

    if not results:
        # 3c — still nothing: stop here, do NOT call the styling tools.
        price_part = f" under ${parsed['max_price']:g}" if parsed["max_price"] else ""
        session["error"] = (
            f"No listings matched '{parsed['description']}'{price_part}. "
            "Try broader terms, a higher budget, or removing the size."
        )
        return session

    # 3a — pick the top-ranked result and pass it forward via session state.
    session["selected_item"] = results[0]

    # Step 4 — suggest an outfit using the selected item + wardrobe.
    session["outfit_suggestion"] = suggest_outfit(
        session["selected_item"], session["wardrobe"]
    )

    # Step 5 — turn the outfit into a shareable fit card.
    session["fit_card"] = create_fit_card(
        session["outfit_suggestion"], session["selected_item"]
    )

    # Step 6 — return the completed session.
    return session


# ── CLI test ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    from utils.data_loader import get_example_wardrobe, get_empty_wardrobe

    print("=== Happy path: graphic tee ===\n")
    session = run_agent(
        query="looking for a vintage graphic tee under $30",
        wardrobe=get_example_wardrobe(),
    )
    if session["error"]:
        print(f"Error: {session['error']}")
    else:
        print(f"Found: {session['selected_item']['title']}")
        print(f"\nOutfit: {session['outfit_suggestion']}")
        print(f"\nFit card: {session['fit_card']}")

    print("\n\n=== No-results path ===\n")
    session2 = run_agent(
        query="designer ballgown size XXS under $5",
        wardrobe=get_example_wardrobe(),
    )
    print(f"Error message: {session2['error']}")
