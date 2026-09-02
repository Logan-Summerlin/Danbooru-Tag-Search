"""Live sanity checks for Step 1 of the Danbooru tag discovery plan."""

from danbooru_client import check_tags_exist, fuzzy_lookup, summarize_tag


def test_known_real_tags() -> None:
    result = check_tags_exist(
        ["1girl", "solo", "blue_eyes", "this_tag_does_not_exist_zzz"]
    )
    assert "1girl" in result and result["1girl"]["post_count"] > 100_000
    assert "solo" in result
    assert "blue_eyes" in result
    assert "this_tag_does_not_exist_zzz" not in result
    print("PASS: existence check")


def test_summary() -> None:
    summary = summarize_tag("blue_eyes")
    assert summary["exists"]
    assert summary["top_co_occurring_general"]
    print("PASS: summary ->", summary["top_co_occurring_general"][:5])


def test_fuzzy() -> None:
    near = fuzzy_lookup("bloo_eyess")
    assert any(row["name"] == "blue_eyes" for row in near)
    print("PASS: fuzzy recovery ->", [row["name"] for row in near])


if __name__ == "__main__":
    test_known_real_tags()
    test_summary()
    test_fuzzy()
