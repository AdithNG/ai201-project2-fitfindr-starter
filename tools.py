"""
tools.py

The three required FitFindr tools. Each tool is a standalone function that
can be called and tested independently before being wired into the agent loop.

Tools:
    search_listings(description, size, max_price)  -> list[dict]
    suggest_outfit(new_item, wardrobe)              -> str
    create_fit_card(outfit, new_item)               -> str
"""

import os
import re

from dotenv import load_dotenv
from groq import Groq

from utils.data_loader import load_listings

load_dotenv()

MODEL = "llama-3.3-70b-versatile"

# Words too common to count as relevance signal in a search query.
_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "but", "by", "for", "how",
    "i", "in", "is", "it", "its", "me", "my", "of", "on", "or", "out",
    "s", "so", "that", "the", "there", "they", "this", "to", "under",
    "up", "want", "was", "what", "whats", "would", "with", "wear", "looking",
    "mostly", "you", "your", "some", "find", "need", "really", "just",
}


# -- text helpers --------------------------------------------------------------

def _fix_mojibake(text):
    """
    Repair double-encoded UTF-8 (e.g. an em-dash showing up as 'a-EUR-"').
    Returns the input unchanged if it isn't a string or can't be repaired.
    """
    if not isinstance(text, str):
        return text
    try:
        return text.encode("cp1252").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return text


def _clean_listing(listing: dict) -> dict:
    """Return a shallow copy of a listing with title/description de-mojibaked."""
    cleaned = dict(listing)
    cleaned["title"] = _fix_mojibake(cleaned.get("title", ""))
    cleaned["description"] = _fix_mojibake(cleaned.get("description", ""))
    return cleaned


def _tokenize(text: str) -> set[str]:
    """Lowercase word tokens with stopwords removed."""
    words = re.findall(r"[a-z0-9]+", text.lower())
    return {w for w in words if w not in _STOPWORDS}


# -- Groq client ---------------------------------------------------------------

def _get_groq_client():
    """Initialize and return a Groq client using GROQ_API_KEY from .env."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        raise ValueError(
            "GROQ_API_KEY not set. Add it to a .env file in the project root."
        )
    return Groq(api_key=api_key)


def _chat(prompt: str, temperature: float, max_tokens: int = 320,
          system: str | None = None) -> str:
    """Single-turn chat completion against the configured Groq model."""
    client = _get_groq_client()
    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})
    resp = client.chat.completions.create(
        model=MODEL,
        messages=messages,
        temperature=temperature,
        max_tokens=max_tokens,
    )
    return (resp.choices[0].message.content or "").strip()


# -- Tool 1: search_listings ---------------------------------------------------

def search_listings(
    description: str,
    size: str | None = None,
    max_price: float | None = None,
) -> list[dict]:
    """
    Search the mock listings dataset for items matching the description,
    optional size, and optional price ceiling.

    Returns a list of matching listing dicts sorted by relevance (best first),
    or an empty list if nothing matches. Never raises for a no-match query.
    """
    listings = load_listings()
    query_tokens = _tokenize(description or "")
    size_q = size.strip().lower() if size and size.strip() else None

    scored: list[tuple[int, float, dict]] = []
    for raw in listings:
        item = _clean_listing(raw)

        # price filter (inclusive)
        if max_price is not None and item.get("price", 0) > max_price:
            continue

        # size filter: case-insensitive substring match in either direction
        if size_q is not None:
            item_size = str(item.get("size", "")).lower()
            if not (size_q in item_size or item_size in size_q):
                continue

        # relevance: count query tokens present in the listing's text fields
        haystack = " ".join([
            item.get("title", ""),
            item.get("description", ""),
            item.get("category", ""),
            item.get("brand") or "",
            " ".join(item.get("style_tags", [])),
            " ".join(item.get("colors", [])),
        ])
        listing_tokens = _tokenize(haystack)
        score = len(query_tokens & listing_tokens)

        if score == 0:
            continue
        scored.append((score, item.get("price", 0.0), item))

    # highest score first; ties broken by lower price
    scored.sort(key=lambda t: (-t[0], t[1]))
    return [item for _, _, item in scored]


# -- Tool 2: suggest_outfit ----------------------------------------------------

def suggest_outfit(new_item: dict, wardrobe: dict) -> str:
    """
    Given a thrifted item and the user's wardrobe, suggest 1-2 complete outfits.

    With a populated wardrobe, references specific named pieces. With an empty
    wardrobe, returns general styling advice. Always returns a non-empty string.
    """
    item = _clean_listing(new_item)
    item_desc = (
        f"{item.get('title', 'this piece')} "
        f"(category: {item.get('category', 'n/a')}, "
        f"colors: {', '.join(item.get('colors', [])) or 'n/a'}, "
        f"style: {', '.join(item.get('style_tags', [])) or 'n/a'})"
    )

    items = (wardrobe or {}).get("items", [])

    try:
        if not items:
            prompt = (
                f"A shopper is considering this secondhand piece: {item_desc}. "
                "They have not shared their wardrobe yet. Give general styling "
                "advice in 2-4 sentences: what categories, colors, and "
                "silhouettes pair well with it and what overall vibe it suits. "
                "Be concrete; do not invent items they own."
            )
            return _chat(prompt, temperature=0.7)

        wardrobe_lines = "\n".join(
            f"- {w.get('name', 'item')} "
            f"({w.get('category', '?')}; "
            f"{', '.join(w.get('colors', [])) or 'n/a'}; "
            f"{', '.join(w.get('style_tags', [])) or 'n/a'})"
            for w in items
        )
        prompt = (
            f"New secondhand piece the shopper is considering:\n{item_desc}\n\n"
            f"Their current wardrobe:\n{wardrobe_lines}\n\n"
            "Suggest 1-2 complete outfits that pair the new piece with specific "
            "items from their wardrobe. Refer to wardrobe pieces by name. Keep it "
            "to 2-4 sentences total, casual and practical, and name the overall vibe."
        )
        return _chat(prompt, temperature=0.7)
    except Exception:
        # API/network failure: degrade gracefully so the pipeline can continue.
        return (
            f"Couldn't reach the styling model just now, but {item.get('title', 'this piece')} "
            f"is a versatile {item.get('category', 'piece')} — build a look around it with "
            "neutral basics and a contrasting layer."
        )


# -- Tool 3: create_fit_card ---------------------------------------------------

def create_fit_card(outfit: str, new_item: dict) -> str:
    """
    Generate a short, shareable OOTD-style caption for the thrifted find.

    Returns a 2-4 sentence caption. If `outfit` is empty/whitespace, returns a
    descriptive error string instead of calling the LLM or raising.
    """
    if not outfit or not outfit.strip():
        return (
            "Can't write a fit card without an outfit suggestion — "
            "run suggest_outfit first so there's a look to caption."
        )

    item = _clean_listing(new_item)
    title = item.get("title", "this piece")
    price = item.get("price", "?")
    platform = item.get("platform", "a thrift app")

    prompt = (
        f"Write a casual, authentic Instagram/TikTok caption for an outfit.\n"
        f"Item: {title}\nPrice: ${price}\nPlatform: {platform}\n"
        f"Outfit: {outfit}\n\n"
        "Rules: 2-4 sentences, sound like a real OOTD post (not a product "
        "description), mention the item name, the price, and the platform once "
        "each, and capture the vibe in specific terms. Lowercase and emoji are fine."
    )
    try:
        # higher temperature so repeated calls on the same input read differently
        return _chat(prompt, temperature=1.0, max_tokens=160)
    except Exception:
        return (
            f"thrifted this {title} off {platform} for ${price} and i'm obsessed — "
            "styled it up and it's officially in heavy rotation. full fit pics soon ✨"
        )
