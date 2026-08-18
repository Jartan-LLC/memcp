"""Keyword scoring for the backends that have no model behind them.

`sqlite` and `in_memory` both rank by this, in one place rather than two copies, so
the pair cannot drift and `docs/portability.md`'s claim that they order results
identically stays true by construction.

What it is: token overlap between the query and the stored content, with a small
amount of tolerance for word endings. What it is not: semantic similarity. Nothing
here knows that "linter" and "static analysis" are related — only that "linter" and
"linting" share a stem.

The rules, and why each exists:

- **Tokens are runs of letters and digits.** The previous version split on whitespace
  and asked whether each piece was a *substring* of the content, so `use?` failed
  against `uses` on the punctuation, and the one-letter token `i` from "What linter
  do I use?" matched every memory containing the letter i — giving unrelated
  memories the same score as the right one.
- **Query tokens under three characters are ignored.** They carry no signal and they
  are what made every memory a hit.
- **Two tokens match when they are equal, or share a four-character prefix.** That is
  what connects `linter` to `linting` without pretending to be a stemmer. Four is
  deliberately conservative: three would match `for` to `form` and `the` to `then`.
- **An exact phrase hit scores 1.0.** Quoting the thing you stored should find it.
  Phrases are matched as token sequences, not as substrings, so a one-character query
  cannot phrase-match every memory containing that letter.
"""

from __future__ import annotations

import re

MIN_QUERY_TOKEN = 3
SHARED_PREFIX = 4

_TOKEN = re.compile(r"[a-z0-9]+")


def tokenize(text: str) -> list[str]:
    return _TOKEN.findall(text.lower())


def _matches(query_token: str, content_tokens: set[str]) -> bool:
    if query_token in content_tokens:
        return True
    if len(query_token) < SHARED_PREFIX:
        return False
    prefix = query_token[:SHARED_PREFIX]
    return any(token.startswith(prefix) for token in content_tokens)


def _is_phrase_of(query_tokens: list[str], content_tokens: list[str]) -> bool:
    """Whether the query's tokens appear in the content, in order and adjacent.

    Token sequences rather than a raw substring test, because a substring test is
    what caused the original bug: `i` is inside `linting`, so a one-character query
    would phrase-match nearly every memory. On tokens, `go` matches "learning go this
    year" and `i` matches nothing in "linting" — which is both of the answers we want.
    """
    if not query_tokens:
        return False
    span = len(query_tokens)
    return any(
        content_tokens[i : i + span] == query_tokens for i in range(len(content_tokens) - span + 1)
    )


def score(query: str, content: str) -> float | None:
    """Relevance in [0, 1], or None when the memory is not a hit at all.

    None and 0.0 are different answers: None means "do not return this", which is
    what keeps an unrelated memory out of the results rather than at the bottom of
    them.
    """
    query_tokens = tokenize(query)
    content_tokens = tokenize(content)

    # An exact phrase hit is the strongest signal there is here, and it is checked
    # first so a short quoted query still works when every token is under the floor.
    if _is_phrase_of(query_tokens, content_tokens):
        return 1.0

    scored = [t for t in query_tokens if len(t) >= MIN_QUERY_TOKEN]
    if not scored:
        return None

    unique_content = set(content_tokens)
    matched = sum(1 for token in scored if _matches(token, unique_content))
    if matched == 0:
        return None
    return matched / len(scored)
