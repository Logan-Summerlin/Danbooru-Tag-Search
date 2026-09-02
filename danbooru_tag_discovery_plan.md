# Danbooru Tag Discovery System — Implementation Plan

**Purpose:** Build a tool that lets an LLM guess which Danbooru tags apply to a
described image (or concept), verify each guess against the live Danbooru API,
and iteratively refine guesses using real search results and human hints.

This plan is written for an LLM (e.g. Claude Code) to implement directly. It
covers the API surface you need, the file layout, and working code for each
step. Follow the steps in order — each step's output feeds the next.

---

## 0. API facts you need before writing anything

- **Base URL:** `https://danbooru.donmai.us` (test against `https://testbooru.donmai.us` first if you want a sandbox).
- **Auth:** Not required for read (GET) endpoints. If you have an account, `login`/`api_key` query params raise your rate limit, but anonymous reads work fine for this project.
- **Required header:** Danbooru asks clients to send a descriptive `User-Agent`, e.g. `User-Agent: tag-discovery-bot/1.0 (contact: you@example.com)`. Don't use a bare default library UA.
- **Rate limit:** ~10 req/sec global burst, but sustained usage should stay around **1 req/sec**. Build in a sleep/backoff from the start — don't bolt it on later.
- **Key endpoints:**
  | Endpoint | Use |
  |---|---|
  | `GET /tags.json?search[name]=...` | Exact-name lookup (comma-separated list for batch exact lookups) |
  | `GET /tags.json?search[name_matches]=foo*` | Wildcard/prefix lookup |
  | `GET /tags.json?search[fuzzy_name_matches]=foo&order=similarity` | Fuzzy match for typo/near-miss recovery |
  | `GET /autocomplete.json?search[query]=foo&search[type]=tag_query` | Same engine Danbooru's own search box uses — great for "is this close to a real tag" |
  | `GET /posts.json?tags=foo&limit=N` | Sample posts carrying a tag — use this to build a "what this tag depicts" summary |
  | `GET /related_tag.json?query=foo` | Co-occurring / related tags Danbooru itself computes |
  | `GET /wiki_pages/{tag_name}.json` | Wiki body text describing the tag (great ground truth for meaning) |
  | `GET /tags.json?search[name]=foo&only=name,post_count,category,antecedent_alias` | Cheap batched existence + alias check |
- **Tag categories:** `0`=general, `1`=artist, `3`=copyright, `4`=character, `5`=meta.
- **Aliases matter:** a guessed tag can be "wrong" but still correct in meaning if it's an alias antecedent — the API tells you the canonical name via `antecedent_alias`. Always check this before declaring a guess a dead end.
- Do not scan sequential post/tag IDs. Only query by tag/name — this project never needs to enumerate the whole database.

---

## 1. File layout

```
danbooru_tags/
├── danbooru_client.py      # Step 1: thin API wrapper + rate limiting
├── test_client.py          # Step 1: sanity tests against real tags
├── extract_patterns.py     # Step 2: pulls sample tags, has the LLM derive conventions
├── tag_strategy.md         # Step 2 OUTPUT: naming-convention doc (generated, not hand-written)
├── guess_tags.py           # Step 3-5: the interactive guess/verify/refine loop
└── session_log.jsonl       # running log of guesses + results, for later strategy-doc updates
```

---

## 2. Step 1 — `danbooru_client.py` (query script + existence check)

Build a small wrapper with exactly these functions. Keep it dependency-light (just `requests`).

```python
import time
import requests

BASE_URL = "https://danbooru.donmai.us"
USER_AGENT = "tag-discovery-bot/1.0 (contact: you@example.com)"
MIN_INTERVAL = 1.0  # seconds between requests, sustained rate

_last_call = 0.0

def _get(path, params=None):
    global _last_call
    wait = MIN_INTERVAL - (time.time() - _last_call)
    if wait > 0:
        time.sleep(wait)
    resp = requests.get(
        f"{BASE_URL}{path}",
        params=params or {},
        headers={"User-Agent": USER_AGENT},
        timeout=10,
    )
    _last_call = time.time()
    resp.raise_for_status()
    return resp.json()


def check_tags_exist(names: list[str]) -> dict[str, dict]:
    """Batch exact-name lookup. Returns {name: {post_count, category, is_alias_of}}
    for names that exist; missing names are simply absent from the result."""
    if not names:
        return {}
    joined = ",".join(names)
    data = _get("/tags.json", {
        "search[name]": joined,
        "only": "name,post_count,category,is_deprecated,antecedent_alias[antecedent_name]",
        "limit": 1000,
    })
    result = {}
    for row in data:
        result[row["name"]] = {
            "post_count": row["post_count"],
            "category": row["category"],
            "is_deprecated": row["is_deprecated"],
        }
    return result


def fuzzy_lookup(name: str, limit: int = 5) -> list[dict]:
    """For a guess that doesn't exist exactly, find near matches."""
    return _get("/tags.json", {
        "search[fuzzy_name_matches]": name,
        "order": "similarity",
        "only": "name,post_count,category",
        "limit": limit,
    })


def autocomplete(prefix: str, limit: int = 10) -> list[dict]:
    """Mirrors the search-box autocomplete; good for partial/prefix guesses."""
    return _get("/autocomplete.json", {
        "search[query]": prefix,
        "search[type]": "tag_query",
        "limit": limit,
    })


def sample_posts_for_tag(tag: str, limit: int = 20) -> list[dict]:
    """Pull lightweight metadata (NOT images) for posts carrying this tag,
    to summarize what the tag tends to depict."""
    return _get("/posts.json", {
        "tags": tag,
        "limit": limit,
        "only": "id,rating,score,tag_string_general,tag_string_character,"
                "tag_string_copyright,tag_string_artist,tag_string_meta",
    })


def related_tags(tag: str, category: str | None = None) -> list:
    params = {"query": tag}
    if category:
        params["category"] = category
    return _get("/related_tag.json", params)


def wiki_body(tag: str) -> str | None:
    try:
        data = _get(f"/wiki_pages/{tag}.json")
        return data.get("body")
    except requests.HTTPError:
        return None


def summarize_tag(tag: str) -> dict:
    """One-call convenience: existence + post sample + co-occurring tags,
    turned into a compact text summary an LLM can reason over."""
    exists = check_tags_exist([tag]).get(tag)
    if not exists:
        return {"tag": tag, "exists": False}

    posts = sample_posts_for_tag(tag, limit=20)
    from collections import Counter
    co_general = Counter()
    co_character = Counter()
    co_copyright = Counter()
    ratings = Counter()
    for p in posts:
        co_general.update(p.get("tag_string_general", "").split())
        co_character.update(p.get("tag_string_character", "").split())
        co_copyright.update(p.get("tag_string_copyright", "").split())
        ratings.update([p.get("rating")])
    co_general.pop(tag, None)

    return {
        "tag": tag,
        "exists": True,
        "post_count": exists["post_count"],
        "category": exists["category"],
        "wiki": wiki_body(tag),
        "top_co_occurring_general": co_general.most_common(15),
        "top_characters": co_character.most_common(5),
        "top_copyrights": co_copyright.most_common(5),
        "rating_breakdown": dict(ratings),
    }
```

### Test it with real tags (do this before moving on)

```python
# test_client.py
from danbooru_client import check_tags_exist, summarize_tag, fuzzy_lookup

def test_known_real_tags():
    result = check_tags_exist(["1girl", "solo", "blue_eyes", "this_tag_does_not_exist_zzz"])
    assert "1girl" in result and result["1girl"]["post_count"] > 100000
    assert "solo" in result
    assert "this_tag_does_not_exist_zzz" not in result
    print("PASS: existence check")

def test_summary():
    s = summarize_tag("blue_eyes")
    assert s["exists"]
    assert s["top_co_occurring_general"]
    print("PASS: summary ->", s["top_co_occurring_general"][:5])

def test_fuzzy():
    near = fuzzy_lookup("bloo_eyess")
    assert any(r["name"] == "blue_eyes" for r in near)
    print("PASS: fuzzy recovery ->", [r["name"] for r in near])

if __name__ == "__main__":
    test_known_real_tags()
    test_summary()
    test_fuzzy()
```

Run this and confirm all three pass before writing anything else. If `check_tags_exist` doesn't behave as expected for comma-joined names on the live API, fall back to one request per name (still fine at 1 req/sec for small batches) — note this in the script as a `TODO` rather than silently guessing.

---

## 3. Step 2 — Extract tagging patterns from real examples

Goal: produce `tag_strategy.md`, a document the guessing LLM will read before
every guessing session. This step is run once (and can be re-run/updated
later), not per-query.

### `extract_patterns.py` — what it does

1. Pick ~25 tags spanning all five categories and a range of `post_count`
   (a few huge generic tags, a few mid-size, a few niche) — e.g. mix of:
   general (`smile`, `looking_at_viewer`, `hair_ribbon`, `sitting`),
   character (2–3 well-known characters), copyright (2–3 series),
   artist (1–2), meta (`highres`, `translated`, `official_art`).
2. For each, call `summarize_tag(tag)` from Step 1's client.
3. Feed all 25 summaries + wiki bodies to the LLM in one prompt and ask it to
   write `tag_strategy.md` covering:
   - **Naming mechanics**: spaces → underscores; lowercase; punctuation handling (`:`, `.`, `'`, `!`); how disambiguation works (`name_(series)` for characters/objects that collide).
   - **Category cues**: what distinguishes a general tag from a meta tag from a character tag by how it reads.
   - **Compositional patterns**: how attributes stack (e.g. `color + noun`: `blue_eyes`, `long_hair`; `verb-ing` for pose/action: `sitting`, `smiling`, `holding_sword`; counting tags: `1girl`, `2boys`, `multiple_girls`).
   - **Aliases & synonyms**: note that near-synonyms often collapse to one canonical tag via alias — the strategy doc should tell the guessing LLM "if your first guess doesn't exist, try the alias-check step before giving up," not just list examples.
   - **Common high-value meta/quality tags** worth guessing by default (`highres`, `absurdres`, `translated`, `commentary`, etc.) since they're cheap, common guesses.
   - **Rating & safety tags** (`rating:g/s/q/e`) as a category, described only functionally (what they filter), not as content to reproduce.

4. Save the LLM's output verbatim as `tag_strategy.md`. This is the guessing LLM's reference doc — keep it pattern-level and reusable, not a dump of the 25 raw examples.

This step only needs to run once per project setup; re-run it later if guesses are consistently missing a pattern (see Step 5 feedback loop).

---

## 4. Step 3–5 — The guess / verify / refine loop (`guess_tags.py`)

This is the interactive core. Structure it as a loop, not a one-shot script,
since Step 5 explicitly requires re-guessing with new hints.

### Loop contract

```python
def guessing_session(description: str, hints: list[str] = None):
    """
    description: free-text summary of what the image(s) show
    hints: optional extra hints from the user, accumulated across rounds
    """
    strategy_doc = open("tag_strategy.md").read()

    while True:
        # --- Step 3: LLM proposes candidates ---
        candidates = llm_propose_tags(description, hints, strategy_doc)
        # candidates: list of (guessed_tag_name, confidence 0-1), most confident first

        # --- verify against the API ---
        names = [c[0] for c in candidates]
        existing = check_tags_exist(names)

        results = []
        for name, confidence in candidates:
            if name in existing:
                results.append({
                    "guess": name, "confidence": confidence,
                    "status": "confirmed",
                    "post_count": existing[name]["post_count"],
                })
            else:
                near = fuzzy_lookup(name, limit=3)
                results.append({
                    "guess": name, "confidence": confidence,
                    "status": "not_found",
                    "closest_matches": [n["name"] for n in near],
                })

        # --- Step 4: present top 10 ---
        top10 = sorted(results, key=lambda r: -r["confidence"])[:10]
        present_to_user(top10)
        log_round(description, hints, top10)   # append to session_log.jsonl

        # --- Step 5: refine or stop ---
        user_input = get_user_feedback()  # "good, done" / new hint / "try again"
        if user_input in ("done", "good", "stop"):
            return top10
        hints = (hints or []) + [user_input]
```

### `llm_propose_tags` prompt template

Use something close to this as the system/instruction prompt for the
proposing LLM call each round:

```
You are guessing Danbooru tags for an image based on a description.

Reference strategy document (naming conventions derived from real tags):
<tag_strategy.md contents>

Image description:
<description>

Additional hints from the user so far:
<hints, newest last>

Prior rounds' results (tags already confirmed or ruled out — don't repeat):
<summary of session_log so far, if any>

Propose up to 15 candidate Danbooru tag names, ranked most→least confident.
Follow Danbooru naming mechanics exactly (lowercase, underscores, parenthetical
disambiguation where relevant). Prefer specific tags over vague ones. Include
at least 2-3 meta/quality tags if plausible (highres, translated, etc.) and
at least 1-2 broader/safer fallback guesses in case specific ones don't exist.

Output as a JSON list of [tag_name, confidence_0_to_1] pairs.
```

### Presenting results (Step 4)

For each of the top 10, show: the guess, confidence, confirmed/not-found
status, post_count if confirmed, and closest real matches if not found. This
gives the human (or calling process) enough to pick a good next hint — e.g.
"there's no tag for 'red dress' exactly, but 'red_dress' isn't real either —
closest match is 'dress' + 'red_theme'."

### Refining (Step 5)

When the user supplies a new hint (e.g. "it's actually a school uniform, not
a dress" or "focus on the pose, not the clothing"), re-run
`llm_propose_tags` with the accumulated hint list and the log of
already-ruled-out guesses, so the LLM doesn't repeat dead ends.

---

## 5. Closing the loop: updating the strategy doc

Append every round's `(guess, status)` pairs to `session_log.jsonl`. Periodically
(e.g. every 50 sessions, or on request), re-run a lightweight version of Step 2:
feed the LLM a sample of guesses that were consistently wrong in the same way,
and ask it to propose a small amendment to `tag_strategy.md`. Keep amendments
additive — append a dated "Amendments" section rather than rewriting the doc,
so you can see what changed and why.

---

## 6. Build order checklist

1. [ ] Write `danbooru_client.py`, run `test_client.py`, confirm all three tests pass against the live API.
2. [ ] Write `extract_patterns.py`, run it once, review `tag_strategy.md` for accuracy before trusting it.
3. [ ] Write `guess_tags.py` with the loop above; do one manual end-to-end session on a description where you already know the right tags, to validate the whole pipeline.
4. [ ] Confirm rate limiting is actually being respected (add a print of elapsed time between calls during testing).
5. [ ] Only after 1–4 pass, use the tool for real unknown-tag discovery sessions.
