"""Keyword scoring for the backends that have no model behind them.

`sqlite` and `in_memory` both rank by this, so they order results identically.

What it is: token overlap between the query and the stored content, with a small
amount of tolerance for word endings. What it is not: semantic similarity. Nothing
here knows that "linter" and "static analysis" are related — only that "linter" and
"linting" share a stem.

The rules, and why each exists:

- **Tokens are runs of letters, digits and combining marks, in any script**, after NFKC
  and lowercasing. A character of a script written without spaces between words (Han,
  kana, Hangul, Thai, Lao, Khmer, Myanmar) is a token of its own, and a run of them in a
  query must appear as a run. The previous version split on whitespace
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
import unicodedata

MIN_QUERY_TOKEN = 3
SHARED_PREFIX = 4

# Scripts written without spaces between words, plus Hangul, whose nouns carry their
# particles: Thai, Lao, Myanmar, Hangul Jamo, Khmer, kana, CJK, Hangul syllables.
_UNSPACED = re.compile(
    "[\u0e00-\u0eff\u1000-\u109f\u1100-\u11ff\u1780-\u17ff\u3040-\u30ff"
    "\u3130-\u318f\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff]"
)
_ASCII_TOKEN = re.compile(r"[a-z0-9]+")


def _is_mark(char: str) -> bool:
    return unicodedata.category(char).startswith("M")


def _tokenize_any(text: str) -> list[str]:
    tokens: list[str] = []
    word: list[str] = []

    def flush() -> None:
        if word:
            tokens.append("".join(word))
            word.clear()

    for char in unicodedata.normalize("NFKC", text).lower():
        if _UNSPACED.match(char):
            flush()
            if char.isalnum() or _is_mark(char):
                tokens.append(char)
        elif char.isalnum():
            word.append(char)
        elif word and _is_mark(char):
            word.append(char)  # a mark belongs to the letter before it, and alone to none
        else:
            flush()
    flush()
    return tokens


def tokenize(text: str) -> list[str]:
    # ASCII gives the same tokens through the regex, several times faster; every row
    # of a search is scored.
    if text.isascii():
        return _ASCII_TOKEN.findall(text.lower())
    return _tokenize_any(text)


def _is_run(unit: list[str]) -> bool:
    return bool(_UNSPACED.match(unit[0]))


def _units(tokens: list[str]) -> list[list[str]]:
    """The query's scoring units: each word, and each run of unspaced-script tokens."""
    units: list[list[str]] = []
    run: list[str] = []
    for token in tokens:
        if _UNSPACED.match(token):
            run.append(token)
            continue
        if run:
            units.append(run)
            run = []
        units.append([token])
    if run:
        units.append(run)
    return units


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

    # A run counts whatever its length and must appear as a run; a word counts from
    # MIN_QUERY_TOKEN characters and may match by prefix.
    scored = [u for u in _units(query_tokens) if _is_run(u) or len(u[0]) >= MIN_QUERY_TOKEN]
    if not scored:
        return None

    unique_content = set(content_tokens)
    matched = sum(
        1
        for unit in scored
        if (
            _is_phrase_of(unit, content_tokens)
            if _is_run(unit)
            else _matches(unit[0], unique_content)
        )
    )
    if matched == 0:
        return None
    return matched / len(scored)
