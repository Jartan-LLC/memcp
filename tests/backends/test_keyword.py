"""The keyword scorer, pinned against the cases that were wrong.

Every case in the first class is one Wren measured on JAR-383 against the previous
substring-on-whitespace implementation. They are here as tests rather than as a
changelog line because the failure mode was silent: nothing errored, the right answer
was still in the results, and it was tied with memories that had nothing to do with
the question.
"""

from __future__ import annotations

import pytest

from memcp.backends.in_memory import InMemoryBackend
from memcp.backends.keyword import score, tokenize

from .keyword_cases import CASES, MISSES

STORED = "Prefers Python 3.12 and uses ruff for linting"
QUESTION = "What linter do I use?"


def _outranks(query: str, right: str, wrong: str) -> bool:
    found = score(query, right)
    return found is not None and found > (score(query, wrong) or 0.0)


class TestTheReadmeWalkthrough:
    """The example in README "Try it" has to work on sqlite, the quickstart's backend."""

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


class TestScriptsBeyondAscii:
    """A word is letters, digits and combining marks in any script (#146)."""

    def test_accented_and_cyrillic_words_stay_whole(self):
        assert tokenize("Café Zürich naïve") == ["café", "zürich", "naïve"]
        assert tokenize("Я живу в Москва") == ["я", "живу", "в", "москва"]

    def test_a_cyrillic_word_is_found(self):
        assert score("Москва", "Я живу в Москва") is not None

    def test_an_accented_word_doesnt_match_its_ascii_fragments(self):
        assert score("Zürich office", "Meeting Richard at the office") == 0.5

    def test_composed_and_decomposed_forms_match(self):
        assert score("café", "cafe\u0301 au lait") is not None

    def test_a_word_with_combining_marks_stays_whole(self):
        assert len(tokenize("मैं हिन्दी बोलता हूँ")) == 4
        assert score("हिन्दी", "मैं हिन्दी बोलता हूँ") is not None

    @pytest.mark.parametrize(
        ("query", "content"),
        [("東京", "私は東京に住んでいます"), ("กรุงเทพ", "ฉันอยู่กรุงเทพมหานคร")],
        ids=["japanese", "thai"],
    )
    def test_a_run_in_an_unspaced_script_is_found_as_a_phrase(self, query: str, content: str):
        assert score(query, content) == 1.0

    def test_an_absent_run_in_an_unspaced_script_is_not_found(self):
        assert score("大阪", "私は東京に住んでいます") is None

    @pytest.mark.parametrize(
        ("query", "content"),
        [("서울", "서울에 살아요"), ("부산시", "부산시에서 일해요")],
        ids=["two syllables", "three syllables"],
    )
    def test_a_korean_noun_before_a_particle_is_found(self, query: str, content: str):
        assert score(query, content) == 1.0

    def test_punctuation_in_an_unspaced_script_separates(self):
        assert score("ジョンスミス", "ジョン・スミス") == 1.0
        assert "・" not in tokenize("ジョン・スミス")

    def test_a_mark_with_nothing_before_it_is_not_a_token(self):
        assert tokenize("❤\ufe0f love") == ["love"]
        assert score("❤\ufe0f", "❤\ufe0f unrelated") is None

    def test_the_unspaced_part_of_a_mixed_query_counts(self):
        assert score("東京 office", "Osaka office") == 0.5
        assert score("東京 office", "東京 office move") == 1.0

    @pytest.mark.parametrize(
        ("query", "content"),
        [
            ("서울 날씨", "서울의 날씨는 맑다"),
            ("날씨 서울", "서울의 날씨는 맑다"),
            ("부산 회의", "부산에서 회의가 있어요"),
            ("東京 ラーメン", "東京で美味しいラーメンを食べた"),
        ],
        ids=["korean", "korean reordered", "korean particles", "japanese"],
    )
    def test_each_run_of_a_multi_word_query_is_matched_on_its_own(self, query: str, content: str):
        assert score(query, content) == 1.0

    def test_a_query_run_doesnt_match_across_a_separator_in_the_content(self):
        assert score("京大", "東京、大阪に行く") is None

    @pytest.mark.parametrize(
        ("query", "right", "wrong"),
        [
            ("3月", "3月の会議", "5月の会議"),
            ("3月", "3月の会議", "5月3日の会議"),
            ("3時", "3時に会う", "13時に会う"),
            ("10時", "10時に会う", "3時に会う"),
            ("第3回会議", "第3回会議", "第5回会議"),
            ("A社の件", "A社の件", "B社の件"),
            ("3월", "3월 회의", "5월 회의"),
            ("2024年3月の売上", "2024年3月の売上", "2024年5月の売上"),
            ("1-2月", "1-2月の予定", "3-2月の予定"),
            ("A-B社", "A-B社との契約", "C-B社との契約"),
            ("in 東京", "東京で会議", "大阪で会議"),
        ],
        ids=[
            "month",
            "day of another month",
            "hour in a longer number",
            "hour",
            "ordinal",
            "latin letter",
            "korean",
            "date",
            "months",
            "companies",
            "english word",
        ],
    )
    def test_a_short_word_in_a_query_with_a_run_tells_memories_apart(
        self, query: str, right: str, wrong: str
    ):
        assert _outranks(query, right, wrong)

    @pytest.mark.parametrize(
        ("query", "content"),
        [
            ("Python으로", "Python을 주로 쓴다"),
            ("Dockerの設定", "Docker設定は環境変数で"),
            ("東京2024", "2024年の東京で会議"),
            ("iPhone用", "iPhone 15用のケース"),
            ("Python3系", "Python 3系に移行"),
            ("用Python写测试", "我用 Python 写测试"),
        ],
        ids=["korean", "japanese particle", "number", "product", "version", "chinese"],
    )
    def test_a_long_word_next_to_a_run_is_found_apart_from_it(self, query: str, content: str):
        assert score(query, content) is not None

    @pytest.mark.parametrize(
        ("query", "content"),
        [
            ("3月", "3 月の会議"),
            ("用AI写代码", "我用 AI 写代码"),
            ("AIの使い方", "AI の使い方を学ぶ"),
            ("v2対応", "v2 対応済み"),
            ("3วัน", "อีก 3 วัน"),
        ],
        ids=["month", "chinese", "japanese", "version", "thai"],
    )
    def test_a_memory_may_space_a_word_off_its_run(self, query: str, content: str):
        assert score(query, content) == 1.0

    @pytest.mark.parametrize(
        ("text", "words"),
        [
            ("ราคา100บาท", ["ราคา", "100", "บาท"]),
            ("ใช้AI ช่วยเขียน", ["ใช้", "ai", "ช่วย", "เขียน"]),
            ("ຂ້ອຍຮັກເຈົ້າ", ["ຂ້ອຍ", "ຮັກ", "ເຈົ້າ"]),
            ("ខ្ញុំស្រលាញ់អ្នក", ["ខ្ញុំ", "ស្រលាញ់", "អ្នក"]),
            ("ကျွန်တော်မင်္ဂလာပါ", ["ကျွန်တော်", "မင်္ဂလာ", "ပါ"]),
        ],
        ids=["thai with a number", "thai with latin", "lao", "khmer", "myanmar"],
    )
    def test_southeast_asian_scripts_split_into_dictionary_words(
        self, text: str, words: list[str]
    ):
        assert tokenize(text) == words

    @pytest.mark.parametrize(
        ("text", "words"),
        [
            ("กรุงเทพฯ", ["กรุงเทพ"]),
            ("เด็กๆ เล่น", ["เด็ก", "เล่น"]),
            ("ເດັກໆ", ["ເດັກ"]),
            ("ក្មេងៗ", ["ក្មេង"]),
        ],
        ids=["thai paiyannoi", "thai maiyamok", "lao", "khmer"],
    )
    def test_repetition_and_abbreviation_marks_end_a_word(self, text: str, words: list[str]):
        assert tokenize(text) == words

    def test_a_word_is_found_where_a_memory_repeats_it(self):
        assert score("ช้า", "เดินช้าๆ") == 1.0

    def test_a_southeast_asian_word_matches_the_compound_it_begins(self):
        assert score("กรุงเทพ", "ฉันอยู่กรุงเทพมหานคร") == 1.0
        assert score("กรุงเทพ", "ไปกรุงศรีอยุธยา") is None

    def test_a_short_southeast_asian_word_counts(self):
        assert _outranks("ไป ร้าน", "ฉันไปร้านกาแฟ", "ฉันอยู่ร้านกาแฟ")

    def test_a_short_southeast_asian_word_matches_only_itself(self):
        assert score("ไป", "ไปรษณีย์อยู่ใกล้") is None
        assert score("ไป", "ไปกินข้าว") == 1.0

    @pytest.mark.parametrize("query", ["who is 田中", "what is 寿司 made of", "where is กรุงเทพ"])
    def test_a_lowercase_short_word_in_a_mixed_query_doesnt_count(self, query: str):
        right = {
            "who is 田中": "田中さんは営業部の部長です",
            "what is 寿司 made of": "寿司は米と魚で作る",
            "where is กรุงเทพ": "ฉันอยู่กรุงเทพมหานคร",
        }[query]
        for unrelated in ("The report is ready", "The plan is a draft", "Notes of the week"):
            assert _outranks(query, right, unrelated)

    @pytest.mark.parametrize(
        ("text", "words"),
        [
            ("ราคา๑๐๐บาท", ["ราคา", "๑๐๐", "บาท"]),
            ("ថ្ងៃទី១៥", ["ថ្ងៃទី", "១៥"]),
        ],
        ids=["thai digits", "khmer digits"],
    )
    def test_southeast_asian_digits_split_from_the_words_around_them(
        self, text: str, words: list[str]
    ):
        assert tokenize(text) == words

    @pytest.mark.parametrize(
        ("query", "right"),
        [("Do I like 寿司?", "寿司が大好きです"), ("My 東京 trip", "東京への旅行は楽しかった")],
        ids=["pronoun", "sentence start"],
    )
    def test_a_capitalised_short_word_in_a_mixed_query_doesnt_count(self, query: str, right: str):
        for unrelated in ("I went to the gym", "Do you remember it", "My plan is ready"):
            assert _outranks(query, right, unrelated)

    def test_a_short_acronym_outside_ascii_counts(self):
        assert _outranks("ÖV 時刻表", "ÖV の時刻表", "バスの時刻表")

    def test_a_lowercase_short_word_written_against_a_run_counts(self):
        assert _outranks("db設計", "DBの設計を見直す", "APIの設計を見直す")

    def test_a_short_word_in_a_thai_query_counts(self):
        assert _outranks("ใช้AI", "ใช้ AI ช่วยเขียน", "ใช้มือเขียน")

    def test_a_lone_character_in_a_query_matches_inside_a_run(self):
        assert _outranks("猫 好き", "猫が好きだ", "犬が好きだ")

    def test_an_iteration_mark_belongs_to_its_run(self):
        assert score("佐々木", "木村と佐藤") is None
        assert score("佐々木", "佐々木さんに会う") == 1.0
        assert tokenize("二\u3007\u3007四年") == ["二", "\u3007", "\u3007", "四", "年"]

    def test_han_beyond_the_basic_plane_belongs_to_its_run(self):
        assert _outranks("𠮷野家", "𠮷野家に行く", "野家に行く")
        assert tokenize("𠮷𩸽") == ["𠮷", "𩸽"]

    @pytest.mark.parametrize(
        ("query", "content"),
        [
            ("میروم", "من می\u200cروم"),
            ("می\u200cروم", "من میروم"),
            ("preset", "a pre\u00adset value"),
            ("葛飾", "葛\U000e0100飾区に住む"),
        ],
        ids=[
            "persian zwnj in content",
            "persian zwnj in query",
            "soft hyphen",
            "variation selector",
        ],
    )
    def test_invisible_joiners_and_selectors_dont_split(self, query: str, content: str):
        assert score(query, content) == 1.0

    def test_a_zero_width_space_separates_words(self):
        assert score("bar", "foo\u200bbar") == 1.0

    def test_the_fast_path_tokenizes_as_the_general_path_does(self):
        from memcp.backends.keyword import _tokenize_any

        samples = [
            STORED,
            QUESTION,
            "use? linting_rules 3.12 ABC-def",
            "a  b\tc\nd",
            "",
            "東京 office",
            "word—word café “quoted”",
            "서울 날씨",
            "東京、 大阪 -- 京都",
            "ジョン・スミス and ❤\ufe0f",
            "İุ, lowercasing that yields a mark NFKC would reorder",
            "ราคา๑๐๐บาท ใช้AI, ສະບາຍດີ ខ្ញុំ 3 ကျွန်တော် 東京",
            "3月 と A社, x 東京 ,5時",
        ]
        for text in samples:
            assert tokenize(text) == _tokenize_any(text), text


async def test_search_returns_only_real_hits():
    """The scorer's contract, through a backend rather than directly."""
    backend = InMemoryBackend()
    await backend.add("alice", STORED)
    for noise in ("Likes tea", "Runs a marathon", "Deploys on Tuesdays"):
        await backend.add("alice", noise)

    results = await backend.search("alice", QUESTION)
    assert [m.content for m in results] == [STORED]
    await backend.close()


@pytest.mark.parametrize(
    ("query", "right", "wrong"),
    [
        pytest.param(
            *case,
            marks=[pytest.mark.xfail(strict=True, reason=MISSES[case[:2]])]
            if case[:2] in MISSES
            else [],
        )
        for case in CASES
    ],
)
def test_the_right_memory_outranks_the_near_miss(query: str, right: str, wrong: str):
    assert _outranks(query, right, wrong)
