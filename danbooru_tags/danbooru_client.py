"""Small, rate-limited read-only client for the Danbooru API."""

from __future__ import annotations

import os
import threading
import time
from collections import Counter
from typing import Any

import requests

BASE_URL = "https://danbooru.donmai.us"
USER_AGENT = os.getenv(
    "DANBOORU_USER_AGENT",
    "tag-discovery-bot/1.0 (contact: you@example.com)",
)
MIN_INTERVAL = 1.0  # seconds between requests, sustained rate
REQUEST_TIMEOUT = 10.0

_last_call = 0.0
_rate_limit_lock = threading.Lock()


def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    """GET a Danbooru JSON endpoint while enforcing the sustained request rate."""
    global _last_call

    with _rate_limit_lock:
        wait = MIN_INTERVAL - (time.monotonic() - _last_call)
        if wait > 0:
            time.sleep(wait)

        response = requests.get(
            f"{BASE_URL}{path}",
            params=params or {},
            headers={"User-Agent": USER_AGENT},
            timeout=REQUEST_TIMEOUT,
        )
        _last_call = time.monotonic()

    response.raise_for_status()
    return response.json()


def _exact_name_rows(names: list[str]) -> dict[str, dict[str, Any]]:
    """Perform one case-insensitive exact-name batch request."""
    data = _get(
        "/tags.json",
        {
            "search[name_lower_comma]": ",".join(names),
            "only": "name,post_count,category,is_deprecated,antecedent_alias[antecedent_name]",
            "limit": 1000,
        },
    )

    wanted = {name.casefold(): name for name in names}
    result: dict[str, dict[str, Any]] = {}
    for row in data:
        api_name = row.get("name")
        if not isinstance(api_name, str):
            continue
        requested_name = wanted.get(api_name.casefold())
        if requested_name is None:
            continue

        antecedent = row.get("antecedent_alias")
        is_alias_of = (
            antecedent.get("antecedent_name") if isinstance(antecedent, dict) else None
        )

        result[requested_name] = {
            "post_count": row["post_count"],
            "category": row["category"],
            "is_deprecated": row["is_deprecated"],
            "is_alias_of": is_alias_of,
        }
    return result


def check_tags_exist(names: list[str]) -> dict[str, dict]:
    """Batch exact-name lookup.

    Returns ``{name: {post_count, category, is_deprecated, is_alias_of}}`` for
    names that exist. Missing names are absent from the result, so callers can
    distinguish a real zero-result lookup from a tag whose post count is zero.

    Danbooru's ``name_lower_comma`` search performs a case-insensitive exact
    comparison against each comma-separated name, allowing one request for a
    batch while the client still reconciles results case-insensitively.
    """
    if not names:
        return {}

    unique_names = list(dict.fromkeys(name for name in names if name))
    if not unique_names:
        return {}

    result = _exact_name_rows(unique_names)
    missing = [name for name in unique_names if name not in result]

    # Keep the fallback for defensive compatibility if a future API deployment
    # changes or omits comma-qualified name search behavior.
    for name in missing:
        result.update(_exact_name_rows([name]))

    return result


def fuzzy_lookup(name: str, limit: int = 5) -> list[dict]:
    """For a guess that doesn't exist exactly, find near matches."""
    if limit < 1:
        return []
    return _get(
        "/tags.json",
        {
            "search[fuzzy_name_matches]": name,
            "order": "similarity",
            "only": "name,post_count,category",
            "limit": limit,
        },
    )


def autocomplete(prefix: str, limit: int = 10) -> list[dict]:
    """Mirror Danbooru's tag autocomplete for partial/prefix guesses."""
    if limit < 1:
        return []
    return _get(
        "/autocomplete.json",
        {
            "search[query]": prefix,
            "search[type]": "tag_query",
            "limit": limit,
        },
    )


def sample_posts_for_tag(tag: str, limit: int = 20) -> list[dict]:
    """Pull lightweight metadata (not images) for posts carrying ``tag``."""
    if limit < 1:
        return []
    return _get(
        "/posts.json",
        {
            "tags": tag,
            "limit": limit,
            "only": (
                "id,rating,score,tag_string_general,tag_string_character,"
                "tag_string_copyright,tag_string_artist,tag_string_meta"
            ),
        },
    )


def related_tags(tag: str, category: str | None = None) -> list:
    """Return Danbooru's computed related/co-occurring tags."""
    params: dict[str, Any] = {"query": tag}
    if category:
        params["category"] = category
    return _get("/related_tag.json", params)


def wiki_body(tag: str) -> str | None:
    """Return a tag's wiki body, or ``None`` when the page does not exist."""
    try:
        data = _get(f"/wiki_pages/{tag}.json")
    except requests.HTTPError as exc:
        if exc.response is not None and exc.response.status_code == 404:
            return None
        raise
    return data.get("body")


def summarize_tag(tag: str) -> dict:
    """Summarize a real tag using existence, sample posts, and its wiki."""
    exists = check_tags_exist([tag]).get(tag)
    if not exists:
        return {"tag": tag, "exists": False}

    posts = sample_posts_for_tag(tag, limit=20)
    co_general: Counter[str] = Counter()
    co_character: Counter[str] = Counter()
    co_copyright: Counter[str] = Counter()
    ratings: Counter[Any] = Counter()

    for post in posts:
        co_general.update(post.get("tag_string_general", "").split())
        co_character.update(post.get("tag_string_character", "").split())
        co_copyright.update(post.get("tag_string_copyright", "").split())
        rating = post.get("rating")
        if rating is not None:
            ratings.update([rating])

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
