"""The keyword scorer, pinned against the cases that were wrong.

Every case in the first class is one Wren measured on JAR-383 against the previous
substring-on-whitespace implementation. They are here as tests rather than as a
changelog line because the failure mode was silent: nothing errored, the right answer
was still in the results, and it was tied with memories that had nothing to do with
the question.
"""

from __future__ import annotations

import pytest

from memcp.backend.in_memory import InMemoryBackend
from memcp.backend.keyword import score, tokenize

STORED = "Prefers Python 3.12 and uses ruff for linting"
QUESTION = "What linter do I use?"


class TestTheReadmeWalkthrough:
    """The example in README "Try it" has to work on the backend `memcp up` installs."""

    def test_the_question_finds_the_memory(self):
        assert score(QUESTION, STORED) is not None

    @pytest.mark.parametrize(
        "unrelated",
        [
            "Deploys on Tuesdays",
            "Likes tea",
            "Runs a marathon",
            "Prefers window seats",
        ],
    )
    def test_the_question_does_not_find_anything_else(self, unrelated: str):
        """The old scorer gave each of these 0.2 — the same as the right answer.

        A single-letter query token was a substring of almost any content, so the
        correct memory won on insertion order rather than on relevance.
        """
        assert score(QUESTION, unrelated) is None

    def test_the_right_memory_outranks_everything_else(self):
        others = [score(QUESTION, c) or 0.0 for c in ("Likes tea", "Runs a marathon")]
        assert (score(QUESTION, STORED) or 0.0) > max(others)

    def test_a_word_ending_does_not_lose_the_match(self):
        """`linter` against `linting` returned nothing at all before."""
        assert score("linter", STORED) == 1.0


class TestScoringRules:
    def test_punctuation_does_not_break_a_token(self):
        assert score("ruff!", "uses ruff") == 1.0

    def test_an_exact_phrase_scores_one(self):
        assert score("uses ruff", STORED) == 1.0

    def test_short_query_tokens_are_ignored(self):
        """`i`, `a`, `do` carry no signal and used to match nearly everything."""
        assert score("i", "linting") is None
        assert score("do a", "the quick brown fox") is None

    def test_a_short_token_still_works_as_a_phrase(self):
        """Ignoring short tokens must not make a quoted short query unfindable."""
        assert score("go", "learning go this year") == 1.0

    def test_a_four_character_prefix_is_the_tolerance_limit(self):
        assert score("linting", "linter") == 1.0
        # Three characters would match `for` to `form` and `the` to `then`.
        assert score("form", "for the win") is None

    def test_score_is_the_fraction_of_query_tokens_matched(self):
        assert score("python ruff", "python and ruff") == 1.0
        assert score("python kubernetes", "python and ruff") == 0.5

    def test_no_match_is_none_not_zero(self):
        """None keeps a memory out of the results; 0.0 would rank it last in them."""
        assert score("kubernetes", STORED) is None

    def test_empty_and_whitespace_queries_do_not_match_everything(self):
        assert score("", STORED) is None
        assert score("   ", STORED) is None

    def test_tokenize_splits_on_anything_that_is_not_alphanumeric(self):
        assert tokenize("Python 3.12, ruff!") == ["python", "3", "12", "ruff"]


async def test_search_returns_only_real_hits():
    """The scorer's contract, through a backend rather than directly."""
    backend = InMemoryBackend()
    await backend.add("alice", STORED)
    for noise in ("Likes tea", "Runs a marathon", "Deploys on Tuesdays"):
        await backend.add("alice", noise)

    results = await backend.search("alice", QUESTION)
    assert [m.content for m in results] == [STORED]
    await backend.close()
