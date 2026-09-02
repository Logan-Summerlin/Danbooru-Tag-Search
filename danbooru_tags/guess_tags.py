"""Interactive LLM guess / verify / refine loop for Danbooru tags.

Usage:
    TAG_DISCOVERY_API_KEY=... python -m danbooru_tags.guess_tags "an image description"

The LLM is accessed through any OpenAI-compatible chat-completions endpoint.
Danbooru remains the source of truth: every proposed tag is checked against
its live API before it is presented as confirmed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

import requests

try:  # Support both ``python -m danbooru_tags.guess_tags`` and direct scripts.
    from .danbooru_client import check_tags_exist, fuzzy_lookup
except ImportError:  # pragma: no cover - exercised by direct CLI invocation
    from danbooru_client import check_tags_exist, fuzzy_lookup

BASE_DIR = Path(__file__).resolve().parent
STRATEGY_PATH = BASE_DIR / "tag_strategy.md"
LOG_PATH = BASE_DIR / "session_log.jsonl"
DEFAULT_MODEL = os.getenv("TAG_DISCOVERY_MODEL", "openai/gpt-4o-mini")
DEFAULT_BASE_URL = os.getenv("TAG_DISCOVERY_BASE_URL", "https://openrouter.ai/api/v1")

SYSTEM_PROMPT = """You guess Danbooru tag names from an image description.
Use the strategy document as naming guidance, but Danbooru's live API is the
source of truth. Prefer specific, visually defensible tags. Do not invent tags.
Return ONLY a JSON array of arrays, e.g. [[\"1girl\",0.98],[\"long_hair\",0.8]].
Use lowercase canonical Danbooru names, underscores, and parenthetical
 disambiguation when appropriate. Propose at most 15 candidates. Include
meta/quality tags only when the description supports them; do not blindly add
claims such as translated or highres. Never output prose outside the JSON."""


def _api_key() -> str:
    key = os.getenv("TAG_DISCOVERY_API_KEY") or os.getenv("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError("Set TAG_DISCOVERY_API_KEY or OPENROUTER_API_KEY")
    return key


def _clean_json(text: str) -> Any:
    """Parse strict JSON, tolerating a single fenced block from an LLM."""
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.I)
        text = re.sub(r"\s*```$", "", text)
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        # Recover an array if the provider wrapped it in accidental prose.
        match = re.search(r"\[[\s\S]*\]", text)
        if not match:
            raise ValueError(f"LLM did not return JSON: {text[:500]!r}") from exc
        return json.loads(match.group(0))


def _normalize_candidates(raw: Any) -> list[tuple[str, float]]:
    if not isinstance(raw, list):
        raise ValueError("LLM output must be a JSON list")
    candidates: list[tuple[str, float]] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, (list, tuple)) or len(item) != 2:
            continue
        name, confidence = item
        if not isinstance(name, str):
            continue
        name = name.strip().lower()
        try:
            confidence = float(confidence)
        except (TypeError, ValueError):
            continue
        if not name or name in seen or not 0 <= confidence <= 1:
            continue
        seen.add(name)
        candidates.append((name, confidence))
    return sorted(candidates, key=lambda item: -item[1])[:15]


def llm_propose_tags(
    description: str,
    hints: list[str] | None,
    strategy_doc: str,
    prior_results: list[dict[str, Any]] | None = None,
) -> list[tuple[str, float]]:
    """Ask the configured LLM for ranked candidate tags."""
    prompt = f"""Reference strategy document:
---
{strategy_doc}
---

Image description:
{description}

Additional hints from the user, oldest first:
{json.dumps(hints or [], ensure_ascii=False)}

Prior rounds' results. Do not repeat tags already confirmed or ruled out unless
the new hint materially changes the case for one:
{json.dumps(prior_results or [], ensure_ascii=False)}

Propose up to 15 candidate Danbooru tag names, ranked most to least confident.
Output JSON only as [[tag_name, confidence], ...]."""

    base_url = os.getenv("TAG_DISCOVERY_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
    payload = {
        "model": os.getenv("TAG_DISCOVERY_MODEL", DEFAULT_MODEL),
        "temperature": 0.1,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ],
    }
    response = requests.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {_api_key()}", "Content-Type": "application/json"},
        json=payload,
        timeout=120,
    )
    response.raise_for_status()
    data = response.json()
    try:
        text = data["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError(f"Unexpected LLM response shape: {data!r}") from exc
    return _normalize_candidates(_clean_json(text))


def verify_candidates(candidates: list[tuple[str, float]]) -> list[dict[str, Any]]:
    """Verify every candidate, recovering near misses and aliases."""
    existing = check_tags_exist([name for name, _ in candidates])
    results: list[dict[str, Any]] = []
    for name, confidence in candidates:
        record: dict[str, Any] = {"guess": name, "confidence": confidence}
        found = existing.get(name)
        if found is not None:
            record.update(
                {
                    "status": "confirmed",
                    "post_count": found["post_count"],
                    "category": found["category"],
                    "is_deprecated": found["is_deprecated"],
                }
            )
            if found.get("is_alias_of"):
                record["is_alias_of"] = found["is_alias_of"]
        else:
            near = fuzzy_lookup(name, limit=3)
            record.update(
                {
                    "status": "not_found",
                    "closest_matches": [row.get("name") for row in near if row.get("name")],
                }
            )
        results.append(record)
    return results


def _format_results(results: list[dict[str, Any]]) -> None:
    print("\nTop tag candidates")
    print("=" * 72)
    for index, result in enumerate(results, 1):
        status = result["status"]
        extra = (
            f"posts={result['post_count']} category={result['category']}"
            if status == "confirmed"
            else f"closest={', '.join(result['closest_matches']) or 'none'}"
        )
        alias = f" alias_of={result['is_alias_of']}" if result.get("is_alias_of") else ""
        print(f"{index:2}. {result['guess']:<32} {result['confidence']:.2f} {status:<10} {extra}{alias}")


def log_round(
    description: str,
    hints: list[str],
    results: list[dict[str, Any]],
    round_number: int,
) -> None:
    """Append an auditable JSONL record; never rewrite prior sessions."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "round": round_number,
        "description": description,
        "hints": list(hints),
        "results": results,
    }
    with LOG_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _prior_results_for_session(rounds: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Compact prior state so the LLM avoids repeating dead ends."""
    flattened: list[dict[str, Any]] = []
    seen: set[str] = set()
    for round_data in rounds:
        for result in round_data.get("results", []):
            name = result.get("guess")
            if not isinstance(name, str) or name in seen:
                continue
            seen.add(name)
            flattened.append(
                {
                    "guess": name,
                    "status": result.get("status"),
                    "closest_matches": result.get("closest_matches", []),
                }
            )
    return flattened


def guessing_session(
    description: str,
    hints: list[str] | None = None,
    *,
    input_fn: Callable[[str], str] = input,
) -> list[dict[str, Any]]:
    """Run Steps 3-5 until the user says done/good/stop."""
    if not STRATEGY_PATH.exists():
        raise FileNotFoundError(
            f"Missing {STRATEGY_PATH}. Run extract_patterns.py first."
        )
    strategy_doc = STRATEGY_PATH.read_text(encoding="utf-8")
    accumulated_hints = list(hints or [])
    rounds: list[dict[str, Any]] = []

    while True:
        candidates = llm_propose_tags(
            description,
            accumulated_hints,
            strategy_doc,
            _prior_results_for_session(rounds),
        )
        if not candidates:
            raise RuntimeError("LLM returned no usable tag candidates")
        results = verify_candidates(candidates)
        top10 = sorted(results, key=lambda row: -row["confidence"])[:10]
        _format_results(top10)
        round_number = len(rounds) + 1
        log_round(description, accumulated_hints, top10, round_number)
        rounds.append({"results": top10})

        user_input = input_fn(
            "\nFeedback (new hint, 'try again', or 'done'): "
        ).strip()
        if user_input.lower() in {"done", "good", "stop"}:
            return top10
        if user_input:
            accumulated_hints.append(user_input)


def main() -> int:
    parser = argparse.ArgumentParser(description="Discover and verify Danbooru tags")
    parser.add_argument("description", nargs="+", help="image/concept description")
    parser.add_argument("--hint", action="append", default=[], help="initial refinement hint")
    args = parser.parse_args()
    guessing_session(" ".join(args.description), args.hint)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
