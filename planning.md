# FitFindr — planning.md

> Completed before writing implementation code. Updated before starting the
> retry-with-fallback stretch feature (see Planning Loop, step 3b).

**What FitFindr does (in my own words):** FitFindr takes one natural-language
thrifting request and turns it into a styled, shareable result. It parses the
request into a description + optional size + optional price ceiling, searches a
mock multi-platform listings dataset for the best match, suggests how to wear
that piece with the user's existing wardrobe, and writes a casual caption for
it. Each tool triggers the next only if the previous one produced usable output:
the search result triggers the outfit suggestion, and the outfit suggestion
triggers the fit card. If the search finds nothing — even after loosening the
size filter — the agent stops and tells the user exactly what to change instead
of calling the styling tools with empty input.

---

## Tools

List every tool your agent will use. For each tool, fill in all four fields.
You must have at least 3 tools. The three required tools are listed — add any additional tools below them.

### Tool 1: search_listings

**What it does:**
Searches the 40-item mock listings dataset for pieces matching a free-text
description, filtering by an optional size and an optional maximum price, then
ranks the survivors by how well their text fields overlap the description.
No LLM — pure deterministic Python so the agent's retrieval is testable and fast.

**Input parameters:**
- `description` (str): keywords describing the desired item, e.g. `"vintage graphic tee"`. Required.
- `size` (str | None): size to filter by, e.g. `"M"` or `"8"`. Case-insensitive, matches as a substring in either direction so `"M"` matches `"S/M"`. `None` skips size filtering.
- `max_price` (float | None): inclusive price ceiling in dollars. `None` skips price filtering.

**What it returns:**
A `list[dict]` of matching listings, highest relevance first. Each dict is a full
listing with: `id`, `title`, `description`, `category`, `style_tags` (list),
`size`, `condition`, `price` (float), `colors` (list), `brand`, `platform`.
Returns `[]` (empty list, never an exception) when nothing matches.

**What happens if it fails or returns nothing:**
Returns an empty list. The planning loop owns the recovery: it first retries the
search with the size filter removed (a common over-constraint), and only if that
also returns `[]` does it set a user-facing error and stop. The error names the
description and price tried and suggests broadening terms or raising the budget.

---

### Tool 2: suggest_outfit

**What it does:**
Given the chosen listing and the user's wardrobe, asks the LLM
(`llama-3.3-70b-versatile` via Groq) to propose 1–2 complete, wearable outfits
that pair the new item with specific, named pieces from the wardrobe.

**Input parameters:**
- `new_item` (dict): a listing dict (the top search result the user is considering).
- `wardrobe` (dict): a wardrobe dict with an `items` key holding a list of wardrobe-item dicts (`id`, `name`, `category`, `colors`, `style_tags`, `notes`). May be empty.

**What it returns:**
A non-empty `str` of outfit suggestions. With a populated wardrobe it references
wardrobe pieces by name ("pair with your baggy dark-wash jeans + platform Docs").
With an empty wardrobe it returns general styling advice (what categories,
colors, and silhouettes pair well) instead of failing.

**What happens if it fails or returns nothing:**
The empty-wardrobe branch is handled inside the tool (general-advice prompt), so
it always returns a useful string. If the Groq call itself raises (network/key),
the tool catches it and returns a plain-text fallback message describing the item
so the pipeline can still produce a fit card; the agent surfaces that string.

---

### Tool 3: create_fit_card

**What it does:**
Turns the outfit suggestion + item details into a short, casual, shareable
caption — the kind of thing you'd put under an OOTD post — using a higher LLM
temperature so repeated calls on the same input read differently.

**Input parameters:**
- `outfit` (str): the suggestion string returned by `suggest_outfit`.
- `new_item` (dict): the listing dict, used for the item name, price, and platform.

**What it returns:**
A 2–4 sentence `str` caption that mentions the item name, price, and platform
once each and captures the vibe in specific terms. Casual, not a product blurb.

**What happens if it fails or returns nothing:**
If `outfit` is empty or whitespace-only, it returns a descriptive error string
(`"Can't write a fit card without an outfit suggestion..."`) rather than calling
the LLM or raising. If the Groq call raises, it returns a simple templated
caption built from the item fields so the user still gets shareable text.

---

### Additional Tools (if any)

No separate fourth tool. The **retry-with-loosened-constraints fallback**
(stretch feature) is implemented as a branch *inside the planning loop* rather
than as its own function, because it re-uses `search_listings` with adjusted
arguments — see Planning Loop step 3b.

---

## Planning Loop

**How does your agent decide which tool to call next?**

The loop is driven by what each step returns, stored in a single `session` dict:

1. **Parse.** `_parse_query(query)` uses regex to pull out `max_price`
   (`under $30`, `$30`, `under 30`), `size` (`size M`, `size 8`), and strips
   those phrases to leave a clean `description`. Stored in `session["parsed"]`.
   *(Regex chosen over an LLM call so parsing is deterministic, free, and
   testable; documented here per the agent.py TODO.)*
2. **Search.** Call `search_listings(description, size, max_price)`. Store in
   `session["search_results"]`.
3. **Branch on the search result:**
   - **3a — results found:** set `session["selected_item"] = results[0]` and
     continue to step 4.
   - **3b — empty AND a size was specified (retry / fallback):** re-call
     `search_listings(description, size=None, max_price)`, record what was
     loosened in `session["adjustment_note"]`, and store the new results.
   - **3c — still empty (or empty with no size to drop):** set
     `session["error"]` to a specific message and **return early**. Do **not**
     call `suggest_outfit`.
4. **Suggest.** Call `suggest_outfit(selected_item, wardrobe)`; store in
   `session["outfit_suggestion"]`.
5. **Caption.** Call `create_fit_card(outfit_suggestion, selected_item)`; store
   in `session["fit_card"]`.
6. **Return** the session.

The loop is genuinely conditional: a query that matches runs all three tools; an
impossible query stops after the search; an over-constrained-by-size query takes
the retry branch and reports the adjustment. The tools called depend on the data.

---

## State Management

**How does information from one tool get passed to the next?**

A single `session` dict (created by `_new_session()` in agent.py) is the only
source of truth for one interaction. Each step writes its output to a named field
and the next step reads from it — nothing is re-entered by the user and nothing
is hardcoded between steps.

| Field | Written by | Read by |
|-------|-----------|---------|
| `query` | `_new_session` | parser |
| `parsed` (`description`/`size`/`max_price`) | parser | `search_listings` |
| `search_results` | step 2/3b | step 3 branch |
| `adjustment_note` | step 3b | UI / error text |
| `selected_item` | step 3a | `suggest_outfit`, `create_fit_card` |
| `wardrobe` | `_new_session` | `suggest_outfit` |
| `outfit_suggestion` | `suggest_outfit` | `create_fit_card` |
| `fit_card` | `create_fit_card` | UI |
| `error` | any early-exit branch | UI (checked first) |

The exact dict that `search_listings` returned as `results[0]` is the exact dict
passed into `suggest_outfit` and `create_fit_card` — verifiable by printing
`session["selected_item"]` mid-run.

---

## Error Handling

For each tool, describe the specific failure mode you're handling and what the agent does in response.

| Tool | Failure mode | Agent response |
|------|-------------|----------------|
| search_listings | No results match the query | Auto-retry once with the size filter dropped (step 3b). If still empty, return early with: `"No listings matched '<description>' under $<price>. Try broader terms, a higher budget, or removing the size."` — never calls the styling tools. |
| suggest_outfit | Wardrobe is empty | Tool detects `wardrobe["items"] == []` and returns general styling advice for the piece (pairings, colors, silhouette) instead of erroring; pipeline continues to the fit card. |
| create_fit_card | Outfit input is missing or incomplete | Guards an empty/whitespace `outfit` and returns a descriptive error string (no LLM call, no exception); agent shows that string in the fit-card panel. |

(Both LLM tools also wrap the Groq call in try/except and degrade to a templated
fallback string on API error, so a network/key failure never crashes the agent.)

---

## Architecture

```
                              User query  +  wardrobe choice
                                        │
                                        ▼
                          ┌──────────────────────────┐
                          │       run_agent()         │
                          │   (planning loop)         │◄──────────────┐
                          └──────────────────────────┘               │
                                        │                            │
                          _parse_query(query)  ──► session["parsed"] │
                                        │                            │
                                        ▼                            │
              search_listings(description, size, max_price)          │
                                        │                            │
                       results == []  &&  size given?                │
                          │ yes                  │ no                 │
                          ▼                      │                    │
        ┌─ RETRY: search_listings(.., size=None) ┘                    │
        │  session["adjustment_note"] = "dropped size filter"         │
        │                 │                                           │
        ▼                 ▼                                           │
   results == []     results == [item, ...]                           │
        │                 │                                           │
        ▼                 ▼                                           │
 [ERROR] session["error"] = "No listings matched..."  ── return ──────┘  (early exit)
                          │
                          ▼
        session["selected_item"] = results[0]
                          │
                          ▼
        suggest_outfit(selected_item, wardrobe)
              │  (empty wardrobe → general advice branch, inside tool)
              ▼
        session["outfit_suggestion"] = "..."
                          │
                          ▼
        create_fit_card(outfit_suggestion, selected_item)
              │  (empty outfit → error string, inside tool)
              ▼
        session["fit_card"] = "..."
                          │
                          ▼
                   return session  ───►  app.py handle_query()  ──► 3 UI panels

        SESSION STATE (single dict, read/written at every step above):
        query · parsed · search_results · adjustment_note ·
        selected_item · wardrobe · outfit_suggestion · fit_card · error
```

---

## AI Tool Plan

**Milestone 3 — Individual tool implementations:**
I'll use **Claude (Claude Code)** one tool at a time. For `search_listings`,
I give it the Tool 1 block above (the three params, the ranked-`list[dict]`
return, and the empty-list-not-exception failure mode) plus the field list from
`load_listings()`, and ask for a keyword-overlap scorer that filters by all three
params. Verify before trusting: confirm it filters on price *and* size *and*
description, drops score-0 items, and returns `[]` (not `None`/exception) for
junk queries — then run the three pytest cases. For `suggest_outfit` and
`create_fit_card` I give Claude the Tool 2/Tool 3 blocks and require: the
empty-wardrobe branch, the empty-outfit guard, model `llama-3.3-70b-versatile`,
and higher temperature on the fit card. Verify by eye (does it branch as
specified?) then by running each on real and degenerate inputs.

**Milestone 4 — Planning loop and state management:**
I'll give Claude the **Architecture diagram + the Planning Loop and State
Management sections** above and ask it to implement `run_agent()` to match.
Acceptance checks before I trust it: (1) it branches on `search_results` and can
return early without calling `suggest_outfit`; (2) it implements the step-3b size
retry; (3) every value lives in the `session` dict, nothing re-entered or
hardcoded between steps. Then I run `python agent.py` and confirm the happy path
fills `fit_card` and the no-results path leaves it `None` with `error` set.

---

## A Complete Interaction (Step by Step)

**Example user query:** "I'm looking for a vintage graphic tee under $30. I mostly wear baggy jeans and chunky sneakers. What's out there and how would I style it?"

**Step 0 — Parse:** `_parse_query` extracts `max_price=30.0`, `size=None` (no
size stated), `description="vintage graphic tee i mostly wear baggy jeans and
chunky sneakers what's out there and how would i style it"` (price phrase
stripped). Stored in `session["parsed"]`.

**Step 1 — Search:** `search_listings(description, size=None, max_price=30.0)`
filters to listings ≤ $30, scores each by keyword overlap (tokens like *vintage*,
*graphic*, *tee*, *jeans* hitting titles/descriptions/style_tags), drops
score-0 items, and returns the ranked list. Suppose the top hit is a faded band
tee at $22 on Depop. `session["search_results"]` = that list;
`session["selected_item"]` = the band-tee dict.

**Step 2 — Suggest outfit:** `suggest_outfit(<band tee>, <example wardrobe>)`
sends the item + the 10 wardrobe items to the LLM, which returns something like:
"Pair it with your baggy dark-wash jeans and platform sneakers for a 90s grunge
vibe — half-tuck the front and add the oversized denim jacket on top." Stored in
`session["outfit_suggestion"]`.

**Step 3 — Fit card:** `create_fit_card(<that suggestion>, <band tee>)` returns a
caption like: "thrifted this faded band tee off depop for $22 and it was MADE for
my baggy jeans 🖤 grunge szn fr — full fit in stories." Stored in
`session["fit_card"]`.

**Final output to user:** the Gradio UI shows three panels — the formatted top
listing (title, price, platform, condition, size), the outfit idea, and the fit
card. If the search had returned nothing, only the first panel would show the
error message and the other two would be empty.
