# Danbooru Tag Search

LLM-assisted discovery of canonical Danbooru tags from an image/concept description, with live API verification and iterative human refinement.

## Setup

```bash
python -m pip install -r requirements.txt
```

The client uses the public Danbooru API and sends a descriptive `User-Agent`. Override it with `DANBOORU_USER_AGENT` when desired.

## Step 2: build the strategy

`danbooru_tags/extract_patterns.py` samples live tags across categories and frequency bands, summarizes their wiki/post metadata, sends the summaries to an OpenAI-compatible LLM, and writes the returned Markdown to `danbooru_tags/tag_strategy.md`.

Set:

```bash
export TAG_DISCOVERY_API_KEY="..."
export TAG_DISCOVERY_MODEL="openai/gpt-4o-mini"
# Optional: another OpenAI-compatible endpoint
export TAG_DISCOVERY_BASE_URL="https://openrouter.ai/api/v1"
```

Run:

```bash
python -m danbooru_tags.extract_patterns
```

The checked-in strategy is a bootstrap artifact; rerunning Step 2 replaces it with the provider's current response based on fresh live examples.

## Steps 3-5: guess, verify, refine

```bash
python -m danbooru_tags.guess_tags "a girl standing in a school uniform"
```

Each round:

1. The LLM proposes up to 15 canonical-looking tags.
2. Every candidate is checked against Danbooru's live `/tags.json` endpoint.
3. Missing candidates receive up to three fuzzy near matches.
4. Confirmed results include post count, category, deprecation state, and alias information when available.
5. The top 10 are displayed and appended to `danbooru_tags/session_log.jsonl`.
6. Enter a new hint to run another round, or enter `done`, `good`, or `stop` to finish.

Prior confirmed/ruled-out candidates are passed into later LLM prompts, preventing repetitive dead ends while accumulated user hints remain in order.

## Verification

The live test suite checks known real tags, a guaranteed non-existent tag, a one-character fuzzy near-miss, and the refinement/logging behavior:

```bash
python danbooru_tags/test_client.py
python danbooru_tags/test_discovery.py
```

GitHub Actions runs these tests against the real Danbooru API on every push to the implementation branch and on pull requests to `main`.
