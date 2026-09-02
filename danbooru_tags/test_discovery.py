"""Tests for the Step 3-5 discovery surface.

The live test deliberately verifies a real tag and a one-character near-miss
against Danbooru; the remaining checks avoid LLM calls.
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from danbooru_tags import guess_tags


def test_candidate_normalization() -> None:
    raw = [["blue_eyes", "0.9"], ["blue_eyes", 0.8], ["", 0.5], ["bad", 2]]
    assert guess_tags._normalize_candidates(raw) == [("blue_eyes", 0.9)]
    print("PASS: candidate normalization")


def test_verification_with_live_api() -> None:
    results = guess_tags.verify_candidates(
        [("1girl", 0.99), ("1grl", 0.5)]
    )
    assert results[0]["status"] == "confirmed"
    assert results[0]["post_count"] > 100_000
    assert results[1]["status"] == "not_found"
    assert "1girl" in results[1]["closest_matches"]
    print("PASS: live verification + fuzzy recovery")


def test_refinement_passes_accumulated_context() -> None:
    calls: list[tuple[str, list[str], list[dict]]] = []
    original = guess_tags.llm_propose_tags
    original_log = guess_tags.LOG_PATH
    with tempfile.TemporaryDirectory() as tmp:
        guess_tags.LOG_PATH = Path(tmp) / "session_log.jsonl"
        state = {"round": 0}

        def fake_propose(description, hints, strategy_doc, prior_results=None):
            state["round"] += 1
            calls.append((description, list(hints or []), list(prior_results or [])))
            if state["round"] == 1:
                return [("1girl", 0.9)]
            return [("school_uniform", 0.9)]

        original_verify = guess_tags.verify_candidates
        guess_tags.llm_propose_tags = fake_propose
        guess_tags.verify_candidates = lambda candidates: [
            {
                "guess": candidates[0][0],
                "confidence": candidates[0][1],
                "status": "confirmed",
                "post_count": 1,
                "category": 0,
                "is_deprecated": False,
            }
        ]
        try:
            result = guess_tags.guessing_session(
                "a girl standing",
                input_fn=iter(["it's a school uniform", "done"]).__next__,
            )
        finally:
            guess_tags.llm_propose_tags = original
            guess_tags.verify_candidates = original_verify
            guess_tags.LOG_PATH = original_log

    assert result[0]["guess"] == "school_uniform"
    assert calls[1][1] == ["it's a school uniform"]
    assert calls[1][2][0]["guess"] == "1girl"
    print("PASS: refinement accumulates hints and prior results")


def test_log_is_valid_jsonl() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "session_log.jsonl"
        original = guess_tags.LOG_PATH
        guess_tags.LOG_PATH = path
        try:
            guess_tags.log_round(
                "desc", ["hint"], [{"guess": "1girl", "status": "confirmed"}], 1
            )
        finally:
            guess_tags.LOG_PATH = original
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        assert rows[0]["round"] == 1
        assert rows[0]["results"][0]["status"] == "confirmed"
    print("PASS: JSONL session logging")


if __name__ == "__main__":
    test_candidate_normalization()
    test_verification_with_live_api()
    test_refinement_passes_accumulated_context()
    test_log_is_valid_jsonl()
