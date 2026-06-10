# FitFindr 🛍️

A multi-tool AI agent that helps you find secondhand clothing and figure out how
to wear it. Give it one natural-language request — *"vintage graphic tee under
$30, size M"* — and it searches a mock multi-platform listings dataset, suggests
how to style the best match against your wardrobe, and writes a shareable OOTD
caption for it. When a tool returns nothing useful, the agent recovers or stops
with a clear message instead of crashing.

Built for CodePath AI201, Project 2. The full design spec lives in
[`planning.md`](planning.md) (written before implementation).

---

## Setup

```bash
python -m venv .venv
source .venv/Scripts/activate      # Windows (Git Bash); use .venv\Scripts\activate on cmd
pip install -r requirements.txt
```

Create a `.env` in the repo root (already git-ignored):

```
GROQ_API_KEY=your_key_here
```

Free key at [console.groq.com](https://console.groq.com). FitFindr uses Groq's
`llama-3.3-70b-versatile` for the two LLM-backed tools.

## Run

```bash
python app.py          # launches the Gradio UI (URL printed in the terminal)
python agent.py        # CLI smoke test: happy path + no-results path
pytest tests/          # 15 tests across the tools and the planning loop
```

---

## Tool Inventory

All three tools live in [`tools.py`](tools.py) and can be called/tested in
isolation. Signatures below match the code exactly.

### 1. `search_listings(description, size, max_price) -> list[dict]`
- **Inputs:**
  - `description` (`str`) — keywords describing the desired item.
  - `size` (`str | None`) — size to filter by; case-insensitive substring match in either direction (`"M"` matches `"S/M"`). `None` skips the filter.
  - `max_price` (`float | None`) — inclusive price ceiling. `None` skips the filter.
- **Output:** `list[dict]` of full listing dicts (`id`, `title`, `description`, `category`, `style_tags`, `size`, `condition`, `price`, `colors`, `brand`, `platform`), ranked by relevance (best first). Empty list when nothing matches.
- **Purpose:** Deterministic, no-LLM retrieval over the 40-item dataset. Filters by price/size, then scores each survivor by keyword overlap between the query and the listing's text fields (stopwords removed), drops zero-score items, and sorts by score (ties broken by lower price).

### 2. `suggest_outfit(new_item, wardrobe) -> str`
- **Inputs:**
  - `new_item` (`dict`) — a listing dict (the chosen item).
  - `wardrobe` (`dict`) — a wardrobe with an `items` list (`id`, `name`, `category`, `colors`, `style_tags`, `notes`); may be empty.
- **Output:** A non-empty `str`. With a populated wardrobe, 1–2 outfits referencing wardrobe pieces by name; with an empty wardrobe, general styling advice.
- **Purpose:** Turns a single item into wearable looks grounded in what the user already owns. Uses Groq (`llama-3.3-70b-versatile`, temp 0.7).

### 3. `create_fit_card(outfit, new_item) -> str`
- **Inputs:**
  - `outfit` (`str`) — the suggestion string from `suggest_outfit`.
  - `new_item` (`dict`) — the listing dict (for name, price, platform).
- **Output:** A 2–4 sentence `str` caption mentioning the item name, price, and platform once each. If `outfit` is empty/whitespace, a descriptive error string instead.
- **Purpose:** Writes a casual, shareable OOTD caption. Runs at temperature 1.0 so repeated calls on the same input read differently.

---

## How the Planning Loop Works

`run_agent(query, wardrobe)` in [`agent.py`](agent.py) drives the loop. It is
**conditional** — the tools it calls depend on what each step returns, not a
fixed sequence:

1. **Parse** — `_parse_query()` uses regex (not an LLM) to pull `max_price`
   (`under $30`, `$30`), `size` (`size M`), and the leftover `description`. An
   empty description short-circuits with a prompt to the user.
2. **Search** — calls `search_listings(description, size, max_price)`.
3. **Branch on the result:**
   - **Results found** → `selected_item = results[0]`, continue.
   - **Empty *and* a size was given** → retry `search_listings` with the size
     filter dropped and record an `adjustment_note` (stretch: retry-with-fallback).
   - **Still empty** → set `error` and **return early — the styling tools are
     never called.**
4. **Suggest** — `suggest_outfit(selected_item, wardrobe)`.
5. **Caption** — `create_fit_card(outfit_suggestion, selected_item)`.
6. **Return** the session.

So a matchable query runs all three tools; an impossible query stops after the
search; an over-constrained-by-size query takes the retry branch and tells the
user what was loosened. (See the ASCII diagram in [`planning.md`](planning.md).)

---

## State Management

A single `session` dict (built by `_new_session()`) is the one source of truth
for an interaction. Each step writes its output to a named field and the next
step reads from it — nothing is re-entered by the user or hardcoded between
steps:

| Field | Written by | Read by |
|-------|-----------|---------|
| `query` | `_new_session` | parser |
| `parsed` | parser | `search_listings` |
| `search_results` | search / retry | branch logic |
| `adjustment_note` | retry branch | UI |
| `selected_item` | branch (`results[0]`) | `suggest_outfit`, `create_fit_card` |
| `wardrobe` | `_new_session` | `suggest_outfit` |
| `outfit_suggestion` | `suggest_outfit` | `create_fit_card` |
| `fit_card` | `create_fit_card` | UI |
| `error` | any early-exit | UI (checked first) |

The exact dict returned as `results[0]` is the one passed into both styling
tools. `tests/test_agent.py::test_happy_path_runs_all_tools` asserts
`session["selected_item"] is session["search_results"][0]` to prove state flows
by reference, not re-entry.

---

## Error Handling (per tool, with tested examples)

**`search_listings` — no matches.** Returns `[]`, never raises. The loop first
retries with the size filter dropped; if still empty it returns early without
calling the styling tools.
```
$ python -c "from tools import search_listings; print(search_listings('designer ballgown', size='XXS', max_price=5))"
[]
```
Full agent on the same query:
> ⚠️ No listings matched 'designer ballgown' under $5. Try broader terms, a higher budget, or removing the size.

**`suggest_outfit` — empty wardrobe.** Detects `wardrobe["items"] == []` and
returns general styling advice instead of failing:
> The Y2K Baby Tee in butterfly print would pair well with distressed denim jeans, flowy skirts, or shorts... it suits a laid-back, vintage-inspired vibe, perfect for everyday wear.

**`create_fit_card` — empty outfit.** Guards an empty/whitespace `outfit` and
returns a descriptive string (no LLM call, no exception):
> Can't write a fit card without an outfit suggestion — run suggest_outfit first so there's a look to caption.

Both LLM tools also wrap the Groq call in `try/except` and degrade to a
templated fallback string on API error, so a network/key failure never crashes
the agent. Every failure mode is locked in by tests in
[`tests/`](tests/) (`pytest tests/` → 15 passing).

---

## Spec Reflection

**One way the spec helped:** Writing the State Management table in `planning.md`
before any code meant `run_agent()` was almost mechanical to implement — every
field already had a defined writer and reader, so I never had to stop and decide
how the item should reach `suggest_outfit`. It also gave me the exact assertion
for the state-passing test (`selected_item is search_results[0]`).

**One way implementation diverged:** The spec originally described the no-match
case as a flat "return early with an error." During implementation I realized a
size filter is the most common over-constraint, so I added the **retry-with-
size-dropped branch** (a stretch feature) ahead of the early exit. This made the
planning loop genuinely conditional rather than "search → maybe error," and I
updated `planning.md` (Planning Loop step 3b and the diagram) to match before
finalizing.

---

## AI Usage

**1 — Implementing the three tools (Milestone 3).** I gave Claude (Claude Code)
each tool's spec block from `planning.md` one at a time — the parameter list,
the `list[dict]`/`str` return shape, and the documented failure mode. For
`search_listings` I required it to use `load_listings()` and to return `[]`
(not `None`/exception) on no match. **What I changed:** the first scorer counted
common words like *"the"* and *"with"* as relevance hits, so I added a stopword
set and made size matching bidirectional-substring so `"M"` matches `"S/M"`. I
also added a mojibake-repair helper because the dataset titles contained
double-encoded em-dashes that were leaking into prompts and the UI.

**2 — The planning loop (Milestone 4).** I gave Claude the Architecture diagram
plus the Planning Loop and State Management sections and asked for `run_agent()`
matching them. **What I overrode:** the generated loop initially called
`suggest_outfit` whenever results existed but didn't implement the size-retry
branch, and it parsed the query with a single loose regex that swallowed the
size into the price. I split parsing into separate price/size patterns in
`_parse_query`, added the retry branch with an `adjustment_note`, and verified
the no-results path leaves `outfit_suggestion`/`fit_card` as `None` before
trusting it.

---

## Demo

A complete multi-step interaction (search → suggest_outfit → create_fit_card),
visible state passing, and a triggered failure are demonstrated in the demo
video (recorded separately). To reproduce locally: `python app.py`, then try
`vintage graphic tee under $30` (happy path) and
`designer ballgown size XXS under $5` (no-results error path).
