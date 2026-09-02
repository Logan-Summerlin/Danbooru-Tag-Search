"""Generate the reusable Danbooru tag-strategy document from live examples.

The script deliberately keeps the LLM integration OpenAI-compatible and
configuration-driven so it works with OpenRouter or another compatible API.
The generated response is written verbatim to ``tag_strategy.md``.
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

import requests

try:
    from .danbooru_client import summarize_tag
except ImportError:  # pragma: no cover - direct script execution
    from danbooru_client import summarize_tag

DEFAULT_MODEL = os.getenv("TAG_DISCOVERY_MODEL", "openai/gpt-4o-mini")
DEFAULT_BASE_URL = os.getenv("TAG_DISCOVERY_BASE_URL", "https://openrouter.ai/api/v1")
OUTPUT_PATH = Path(__file__).with_name("tag_strategy.md")

SAMPLE_TAGS = [
    "smile", "looking_at_viewer", "hair_ribbon", "sitting", "long_hair",
    "blue_eyes", "red_dress", "1girl", "2boys", "multiple_girls",
    "highres", "absurdres", "translated", "commentary", "official_art",
    "rating:s", "rating:q", "hatsune_miku", "reimu_hakurei",
    "hakurei_reimu", "touhou", "the_idolm@ster", "artist_request",
    "solo", "full_body",
]

SYSTEM_PROMPT = """You are a technical documentation writer analyzing real Danbooru tag metadata.
Write a concise, reusable strategy document for an LLM that must guess Danbooru
 tags from image descriptions. Derive rules from the supplied live examples;
do not dump the examples. Cover naming mechanics, category cues, compositional
patterns, aliases/synonyms, high-value meta/quality tags, and rating/safety
filters. Be precise about uncertainty: observed examples are evidence, not a
complete grammar. Do not include explicit sexual descriptions or reproduce
post content. Output Markdown only, with no preamble outside the document."""


def _llm_generate(summaries: list[dict[str, Any]]) -> str:
    """Call an OpenAI-compatible chat-completions endpoint and return its text."""
    api_key = os.getenv("TAG_DISCOVERY_API_KEY") or os.getenv("OPENROUTER_API_KEY")
    if not api_key:
        raise RuntimeError(
            "Set TAG_DISCOVERY_API_KEY (or OPENROUTER_API_KEY) to generate "
            "tag_strategy.md."
        )

    model = os.getenv("TAG_DISCOVERY_MODEL", DEFAULT_MODEL)
    base_url = os.getenv("TAG_DISCOVERY_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    payload = {
        "model": model,
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": (
                    "Here are live Danbooru summaries in JSON. Infer reusable "
                    "tagging conventions from them.\n\n"
                    + json.dumps(summaries, ensure_ascii=False, indent=2)
                ),
            },
        ],
    }
    response = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json=payload,
        timeout=120,
    )
    response.raise_for_status()
    data = response.json()
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected LLM response shape: {data!r}") from exc
    if not isinstance(text, str) or not text.strip():
        raise RuntimeError("LLM returned an empty strategy document")
    return text


def collect_summaries() -> list[dict[str, Any]]:
    summaries: list[dict[str, Any]] = []
    for tag in SAMPLE_TAGS:
        print(f"Summarizing {tag}...", file=sys.stderr)
        summary = summarize_tag(tag)
        if summary.get("exists") and not summary.get("is_deprecated", False):
            summaries.append(summary)
    if len(summaries) < 15:
        raise RuntimeError(
            f"Only {len(summaries)} live sample tags were available; refusing "
            "to generate a strategy from an unrepresentative sample."
        )
    return summaries


def main() -> int:
    summaries = collect_summaries()
    strategy = _llm_generate(summaries)
    OUTPUT_PATH.write_text(strategy, encoding="utf-8")
    print(f"Wrote {len(strategy)} characters to {OUTPUT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
