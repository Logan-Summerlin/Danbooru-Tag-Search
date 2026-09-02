"""Live sanity checks for Step 1 of the Danbooru tag discovery plan."""

from danbooru_client import check_tags_exist, fuzzy_lookup, summarize_tag


NONEXISTENT_TAG = "this_tag_does_not_exist_zzz"


def test_known_real_tags() -> None:
    result = check_tags_exist(["1girl", "solo", "blue_eyes", NONEXISTENT_TAG])
    assert "1girl" in result and result["1girl"]["post_count"] > 100_000
    assert "solo" in result
    assert "blue_eyes" in result
    assert NONEXISTENT_TAG not in result
    print("PASS: real-tag existence lookup")


def test_nonexistent_tag_returns_zero_results() -> None:
    result = check_tags_exist([NONEXISTENT_TAG])
    assert result == {}
    summary = summarize_tag(NONEXISTENT_TAG)
    assert summary == {"tag": NONEXISTENT_TAG, "exists": False}
    print("PASS: non-existent tag returns zero results")


def test_summary() -> None:
    summary = summarize_tag("blue_eyes")
    assert summary["exists"]
    assert summary["top_co_occurring_general"]
    print("PASS: summary ->", summary["top_co_occurring_general"][:5])


def test_fuzzy() -> None:
    # This is Danbooru's own upstream fuzzy-match regression case:
    # hatsune_mika -> hatsune_miku.
    near = fuzzy_lookup("hatsune_mika")
    assert any(row["name"] == "hatsune_miku" for row in near)
    print("PASS: fuzzy recovery ->", [row["name"] for row in near])


if __name__ == "__main__":
    test_known_real_tags()
    test_nonexistent_tag_returns_zero_results()
    test_summary()
    test_fuzzy()
