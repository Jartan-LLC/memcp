"""Tests for validation helpers and error types."""

from __future__ import annotations

import pytest

from memcp.types import (
    AUTHOR_METADATA_KEY,
    Memory,
    canonical_error,
    reject_nested_filters,
    serialize_memory,
    split_author,
    strip_reserved_metadata,
    validate_memory_id,
)

# ---------------------------------------------------------------------------
# validate_memory_id
# ---------------------------------------------------------------------------


class TestValidateMemoryId:
    def test_valid_uuid(self):
        assert validate_memory_id("abc-123-def") == "abc-123-def"

    def test_valid_alphanumeric(self):
        assert validate_memory_id("memory_42") == "memory_42"

    def test_empty_string_rejected(self):
        with pytest.raises(ValueError, match="Invalid memory_id"):
            validate_memory_id("")

    def test_too_long_rejected(self):
        with pytest.raises(ValueError, match="Invalid memory_id"):
            validate_memory_id("a" * 129)

    def test_max_length_accepted(self):
        assert validate_memory_id("a" * 128) == "a" * 128

    def test_special_chars_rejected(self):
        with pytest.raises(ValueError, match="Invalid memory_id"):
            validate_memory_id("memory id with spaces")

    def test_path_traversal_rejected(self):
        with pytest.raises(ValueError, match="Invalid memory_id"):
            validate_memory_id("../../etc/passwd")

    def test_url_injection_rejected(self):
        with pytest.raises(ValueError, match="Invalid memory_id"):
            validate_memory_id("id?user_id=attacker")


# ---------------------------------------------------------------------------
# reject_nested_filters
# ---------------------------------------------------------------------------


class TestRejectNestedFilters:
    def test_flat_filters_pass(self):
        reject_nested_filters({"agent_id": "a1", "run_id": "r1"})

    def test_and_rejected(self):
        with pytest.raises(ValueError, match="Nested boolean"):
            reject_nested_filters({"AND": [{"agent_id": "a1"}]})

    def test_or_rejected(self):
        with pytest.raises(ValueError, match="Nested boolean"):
            reject_nested_filters({"OR": [{"a": 1}, {"b": 2}]})

    def test_not_rejected(self):
        with pytest.raises(ValueError, match="Nested boolean"):
            reject_nested_filters({"NOT": {"agent_id": "a1"}})

    def test_case_insensitive(self):
        with pytest.raises(ValueError, match="Nested boolean"):
            reject_nested_filters({"and": [{"a": 1}]})

    def test_empty_dict_passes(self):
        reject_nested_filters({})


# ---------------------------------------------------------------------------
# canonical_error
# ---------------------------------------------------------------------------


class TestCanonicalError:
    def test_structure(self):
        err = canonical_error("not_found", "Memory not found")
        assert err == {
            "error": {"code": "not_found", "message": "Memory not found", "retry": False}
        }

    def test_retry_flag(self):
        err = canonical_error("timeout", "Backend timeout", retry=True)
        assert err["error"]["retry"] is True


# ---------------------------------------------------------------------------
# strip_reserved_metadata / split_author — SEC-2026-0094 conjunct 2
# ---------------------------------------------------------------------------


class TestStripReservedMetadata:
    def test_none_passes_through_as_none(self):
        """None means 'caller supplied no metadata'; turning it into {} would make
        update_memory's metadata-preserving None wipe existing metadata instead."""
        assert strip_reserved_metadata(None) is None

    def test_ordinary_keys_survive(self):
        assert strip_reserved_metadata({"source": "test", "k": 1}) == {"source": "test", "k": 1}

    def test_reserved_key_dropped(self):
        assert strip_reserved_metadata({AUTHOR_METADATA_KEY: "forged"}) == {}

    def test_reserved_key_dropped_alongside_ordinary_keys(self):
        cleaned = strip_reserved_metadata({"k": "v", AUTHOR_METADATA_KEY: "forged"})
        assert cleaned == {"k": "v"}

    def test_any_key_in_the_reserved_namespace_is_dropped(self):
        """Not just the one key memcp defines today — the whole `_memcp_` prefix
        is reserved, per the issue's 'reserved namespace' language."""
        cleaned = strip_reserved_metadata({"_memcp_future_field": "x", "k": "v"})
        assert cleaned == {"k": "v"}

    def test_does_not_mutate_the_input(self):
        original = {AUTHOR_METADATA_KEY: "forged", "k": "v"}
        strip_reserved_metadata(original)
        assert original == {AUTHOR_METADATA_KEY: "forged", "k": "v"}


class TestSplitAuthor:
    def test_no_reserved_key_is_unattributed(self):
        """A row stored before this field existed, or by a caller that bypassed
        attribution — never inferred, never back-filled from anything else."""
        author, metadata = split_author({"source": "test"})
        assert author is None
        assert metadata == {"source": "test"}

    def test_none_metadata_is_unattributed(self):
        author, metadata = split_author(None)
        assert author is None
        assert metadata == {}

    def test_extracts_author_and_strips_it_from_visible_metadata(self):
        author, metadata = split_author({AUTHOR_METADATA_KEY: "agent-one", "k": "v"})
        assert author == "agent-one"
        assert metadata == {"k": "v"}
        assert AUTHOR_METADATA_KEY not in metadata

    def test_non_string_reserved_value_is_ignored_not_trusted(self):
        """Storage is opaque JSON — a corrupted or tampered value under the
        reserved key must not be handed back as if it were a seat label."""
        author, _metadata = split_author({AUTHOR_METADATA_KEY: 12345})
        assert author is None


class TestSerializeMemoryAttribution:
    def test_attributed_row(self):
        wire = serialize_memory(Memory(id="1", content="x", author="agent-one"))
        assert wire["author"] == "agent-one"
        assert wire["attributed"] is True

    def test_unattributed_row_is_null_not_omitted(self):
        wire = serialize_memory(Memory(id="1", content="x"))
        assert wire["author"] is None
        assert wire["attributed"] is False
