# Danbooru Tag Guessing Strategy

This document is the reusable strategy artifact produced from the Step 2 live-example extraction set. It describes patterns rather than memorizing individual posts. Danbooru's live API remains authoritative: a plausible-looking string is not a valid tag until verified.

## Naming mechanics

- Tags are normally lowercase and use underscores instead of spaces: `blue_eyes`, `long_hair`, `looking_at_viewer`.
- Preserve meaningful punctuation when Danbooru uses it; do not invent punctuation merely to make English read naturally. Characters such as `:` can occur in search/filter syntax and should not automatically be treated as ordinary tag-name punctuation.
- Prefer the canonical spelling returned by Danbooru. Do not assume an English synonym is independently tagged.
- When names collide, Danbooru can use parenthetical disambiguation such as `name_(series)`. Treat the API result as authoritative rather than trying to infer the suffix yourself.
- Counting tags are compact numeric forms such as `1girl` and `2boys`; plurals and collective concepts have their own canonical tags.

## Category cues

Danbooru category numbers are 0 general, 1 artist, 3 copyright, 4 character, and 5 meta.

- **General:** visible attributes, objects, actions, poses, composition, clothing, anatomy, colors, and other image-level concepts.
- **Character:** a named fictional/persona character. Expect a canonical character spelling and possible parenthetical disambiguation.
- **Copyright:** a named franchise, series, game, or other source work.
- **Artist:** an artist attribution tag.
- **Meta:** information about the post, source, translation, quality, moderation/search behavior, or other database metadata rather than depicted content.

A semantic guess alone cannot reliably determine the category. Verify the returned category with the API.

## Compositional patterns

Common productive forms include:

- `attribute + noun`: `blue_eyes`, `long_hair`, `hair_ribbon`.
- Actions and poses often use compact gerund/action forms such as `sitting`, `smiling`, `holding_sword`.
- People counts use numeric forms such as `1girl` and `2boys`; use the canonical count tag rather than translating the phrase literally.
- Specific tags should be preferred over vague umbrella concepts when the description supports them. Add broader fallbacks only when they represent a defensible visual fact.
- Do not assume every English phrase maps compositionally to a Danbooru tag. Verify the complete candidate and use autocomplete/fuzzy search to recover the site's preferred decomposition or synonym.

## Aliases and synonyms

A failed exact lookup is not necessarily a failed concept. Danbooru aliases can map an obsolete or synonymous name to a canonical antecedent. Always inspect `is_alias_of` from the existence check before discarding a candidate. If an exact candidate is absent, use fuzzy lookup and autocomplete to discover the canonical spelling; then verify that recovered tag directly.

The guessing loop must record both confirmed tags and dead ends so later rounds do not repeatedly propose the same invalid spelling. A new user hint can justify reconsidering a previously rejected candidate, but otherwise the prior result log should constrain repetition.

## High-value meta and quality tags

Meta/quality candidates are useful only when the description or surrounding workflow provides evidence for them. Common examples worth considering include `highres`, `absurdres`, `translated`, `commentary`, and `official_art`. Do not automatically assert them merely because they are common; existence in Danbooru establishes that a tag exists, not that it applies to the described image.

## Rating and safety filters

Danbooru rating/search filters such as `rating:g`, `rating:s`, `rating:q`, and `rating:e` describe filtering by rating. Treat them as functional search/filter concepts rather than ordinary visual attributes. Only propose a rating when the input description or task explicitly provides enough information to justify one.

## Verification policy

1. Generate canonical-looking candidates.
2. Batch exact-check every candidate against `/tags.json`.
3. For missing candidates, query fuzzy matches and present the closest canonical names.
4. Preserve category, post count, deprecation, and alias information for confirmed tags.
5. Never treat an LLM's confidence as proof that a tag exists or applies.
6. For a real session, use the top verified results plus the closest recovered names to choose the next human hint.

## Refinement policy

Each round receives the original description, all accumulated hints (newest last), and a compact summary of prior confirmed/ruled-out candidates. New hints should change the candidate set rather than merely reorder the old set. Every round is appended to `session_log.jsonl` so repeated failures become evidence for future strategy amendments.

## Amendments

The Step 2 extractor can be rerun against a fresh live sample set. Future strategy changes should be additive and dated here rather than silently replacing these rules, preserving a trace of why the guessing behavior changed.
