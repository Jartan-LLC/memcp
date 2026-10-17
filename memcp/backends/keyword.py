"""Keyword scoring for the backends that have no model behind them.

`sqlite` and `in_memory` both rank by this, so they order results identically.

What it is: token overlap between the query and the stored content, with a small
amount of tolerance for word endings. What it is not: semantic similarity. Nothing
here knows that "linter" and "static analysis" are related — only that "linter" and
"linting" share a stem.

The rules, and why each exists:

- **Tokens are runs of letters, digits and combining marks, in any script**, after NFKC
  and lowercasing. The previous version split on whitespace and asked whether each
  piece was a *substring* of the content, so `use?` failed against `uses` on the
  punctuation, and the one-letter token `i` from "What linter do I use?" matched every
  memory containing the letter i — giving unrelated memories the same score as the
  right one.
- **Han, kana and Hangul match by character pairs.** Han and kana are written without
  spaces between words, and Hangul attaches particles to its words, so each character
  is a token. A run of them in a query scores the share of its overlapping pairs that
  the content holds, as Lucene's CJK bigram filter does; a lone character counts as
  itself. A word written against a run pairs with the character beside it, so `3月`
  ranks `3月の会議` above `5月3日の会議`, and a memory may put a space between them.
  Dictionary segmentation would split a short query differently from a long memory,
  and it leaves Korean particles attached; pairs need no dictionary.
- **Thai, Lao, Khmer and Myanmar split into dictionary words**, by ICU, as Lucene
  splits Thai. Pairs of their characters, many of them vowel and tone marks, rank far
  worse. A word matches exactly, or, from four characters, as the start of a compound:
  `กรุงเทพ` finds `กรุงเทพมหานคร`.
- **Query words under three characters are ignored**, unless the query holds one of
  these scripts and the word is written against a run, in capitals (not `I`), or holds
  a digit; every Thai, Lao, Khmer or Myanmar word counts. They carry no signal and they
  are what made every memory a hit; there, as the `AI` of `AIの使い方` or the `3` of
  `3月`, a short word tells memories apart, while `is`, `My` or `I` would match every
  memory.
- **Two words match when they are equal, or share a four-character prefix.** That is
  what connects `linter` to `linting` without pretending to be a stemmer. Four is
  deliberately conservative: three would match `for` to `form` and `the` to `then`.
- **An exact phrase hit scores 1.0.** Quoting the thing you stored should find it.
  Phrases are matched as token sequences, not as substrings, so a one-character query
  cannot phrase-match every memory containing that letter.
"""

from __future__ import annotations

import re
import unicodedata
from itertools import pairwise

from icu4py.breakers import WordBreaker

MIN_QUERY_TOKEN = 3
SHARED_PREFIX = 4

# Scripts tokenized per character and matched by pairs: written without spaces between
# words (Han, kana), or with particles attached to words (Hangul).
_UNSPACED = re.compile(
    r"[\u1100-\u11ff\u3005-\u3007\u303b\u3040-\u30ff\u3130-\u318f\u31f0-\u31ff"
    r"\u3400-\u4dbf\u4e00-\u9fff\uac00-\ud7af\uf900-\ufaff\U00020000-\U0003ffff]"
)
# Scripts segmented into words by ICU's dictionaries: Thai, Lao, Myanmar, Khmer.
_SEGMENTED = re.compile(r"[\u0e00-\u0eff\u1000-\u109f\u1780-\u17ff]")
_ASCII_TOKEN = re.compile(r"[a-z0-9]+")
# Thai, Lao and Khmer repetition and abbreviation marks (ๆ ฯ ໆ ៗ): ICU keeps them in a word.
_WORD_MARKS = frozenset("\u0e46\u0e2f\u0ec6\u17d7")
# Never a token: marks a space or punctuation next to a per-character run, so the runs on
# either side of it stay apart and no pair spans it.
_BOUNDARY = " "


def _is_mark(char: str) -> bool:
    return unicodedata.category(char).startswith("M")


def _is_run_token(token: str) -> bool:
    return bool(_UNSPACED.match(token))


def _is_segmented(token: str) -> bool:
    return bool(_SEGMENTED.match(token))


def _segment(text: str) -> list[str]:
    return [w for w in WordBreaker(text, "und") if any(c.isalnum() for c in w)]


def _tokenize_any(text: str) -> list[str]:
    return _tokens_of(unicodedata.normalize("NFKC", text).lower())


def _tokens_of(folded: str) -> list[str]:
    """Tokens of text already normalized and lowercased: normalizing again isn't safe,
    since lowercasing can produce marks that NFKC then reorders."""
    tokens: list[str] = []
    word: list[str] = []
    segment: list[str] = []
    gap = False

    def emit(token: str) -> None:
        nonlocal gap
        if gap and tokens and (_is_run_token(tokens[-1]) or _is_run_token(token)):
            tokens.append(_BOUNDARY)
        tokens.append(token)
        gap = False

    def flush() -> None:
        if word:
            emit("".join(word))
            word.clear()
        if segment:
            for segmented in _segment("".join(segment)):
                emit(segmented)
            segment.clear()

    for char in folded:
        if unicodedata.category(char) == "Cf" and char != "\u200b":
            continue  # joiners and soft hyphens sit inside words; a zero-width space ends one
        if char in _WORD_MARKS:
            flush()
            gap = True
            continue
        if _SEGMENTED.match(char) and not char.isdigit():  # ICU glues digits to words
            if word:
                flush()
            segment.append(char)
            continue
        if segment:
            flush()
        if _UNSPACED.match(char):
            flush()
            # Other characters of these blocks, such as U+30FB "・", join the run around them.
            if char.isalnum() or _is_mark(char):
                emit(char)
        elif char.isalnum():
            word.append(char)
        elif _is_mark(char):
            # A mark belongs to the letter before it; after a run character, or alone, it's
            # dropped, so a variation selector doesn't split a run.
            if word:
                word.append(char)
        else:
            flush()
            gap = True
    flush()
    return tokens


def tokenize(text: str) -> list[str]:
    # Every row of a search is scored, so only the pieces holding non-ASCII take the
    # per-character path; the result is the same as _tokenize_any(text).
    if text.isascii():
        return _ASCII_TOKEN.findall(text.lower())
    tokens: list[str] = []
    for piece in unicodedata.normalize("NFKC", text).lower().split():
        part = _ASCII_TOKEN.findall(piece) if piece.isascii() else _tokens_of(piece)
        if not part:
            continue
        if tokens and (_is_run_token(tokens[-1]) or _is_run_token(part[0])):
            tokens.append(_BOUNDARY)
        tokens.extend(part)
    return tokens


def _words_and_runs(tokens: list[str]) -> tuple[list[str], list[list[str]]]:
    """The words, and the per-character runs with any word written against them, so 3月
    pairs its 3 with its 月 and doesn't match the 3 and the 月 of 5月3日."""
    words = [token for token in tokens if token != _BOUNDARY and not _is_run_token(token)]
    groups: list[list[str]] = []
    previous = _BOUNDARY
    for token in tokens:
        if token != _BOUNDARY:
            if previous != _BOUNDARY and (_is_run_token(previous) or _is_run_token(token)):
                groups[-1].append(token)
            else:
                groups.append([token])
        previous = token
    return words, [group for group in groups if any(map(_is_run_token, group))]


def _unspaced(tokens: list[str]) -> list[str]:
    """Content tokens without the boundaries between a word and a run: a memory may space
    a word off the run it's written against, as in "3 月"; the query's spacing counts."""
    return [
        token
        for i, token in enumerate(tokens)
        if token != _BOUNDARY or (_is_run_token(tokens[i - 1]) and _is_run_token(tokens[i + 1]))
    ]


def _pairs(run: list[str]) -> list[str]:
    if len(run) == 1:
        return run
    return [a + b for a, b in pairwise(run)]


def _matches(query_token: str, content_tokens: set[str]) -> bool:
    if query_token in content_tokens:
        return True
    if len(query_token) < SHARED_PREFIX:
        return False
    if _is_segmented(query_token):
        # A compound such as กรุงเทพมหานคร is one word to ICU; the query may name its start.
        return any(token.startswith(query_token) for token in content_tokens)
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

    # Each word and each run is one unit. A word scores 1 or 0, and a run the share of
    # its pairs the content holds, so a near-miss run scores below the right one.
    query_words, query_runs = _words_and_runs(query_tokens)
    counted = [word for word in query_words if len(word) >= MIN_QUERY_TOKEN]
    if query_runs or any(map(_is_segmented, query_words)):
        # A short word tells memories apart when it's written against a run, as the 3 of
        # 3月, or marked, as AI or v2; is, My or I only match every memory.
        attached = {token for run in query_runs for token in run}
        marked = {
            w.lower()
            for w in _tokens_of(unicodedata.normalize("NFKC", query))
            if (w.isupper() and w != "I") or any(c.isdigit() for c in w)
        }
        counted = [
            word
            for word in query_words
            if len(word) >= MIN_QUERY_TOKEN
            or _is_segmented(word)
            or word in attached
            or word in marked
        ]
    if not counted and not query_runs:
        return None

    content_words, content_runs = _words_and_runs(_unspaced(content_tokens))
    unique_words = set(content_words)
    content_pairs = {pair for run in content_runs for pair in _pairs(run)}
    content_pairs.update(token for run in content_runs for token in run if _is_run_token(token))
    credit = sum(1.0 for word in counted if _matches(word, unique_words))
    for run in query_runs:
        pairs = _pairs(run)
        credit += sum(pair in content_pairs for pair in pairs) / len(pairs)
    if credit == 0:
        return None
    return credit / (len(counted) + len(query_runs))
