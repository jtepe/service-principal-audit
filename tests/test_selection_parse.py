"""Tests for the pure selection parsing surface (no network, no IO)."""

from __future__ import annotations

from sp_audit.selection_parse import merge_object_ids, parse_ids_file


def test_parse_ids_file_newline_list() -> None:
    content = "oid-1\noid-2\noid-3\n"
    assert parse_ids_file(content) == ["oid-1", "oid-2", "oid-3"]


def test_parse_ids_file_ignores_blank_lines() -> None:
    content = "oid-1\n\n   \noid-2\n\t\n"
    assert parse_ids_file(content) == ["oid-1", "oid-2"]


def test_parse_ids_file_strips_whitespace_per_line() -> None:
    content = "  oid-1  \n\toid-2\n"
    assert parse_ids_file(content) == ["oid-1", "oid-2"]


def test_parse_ids_file_json_array() -> None:
    content = '["oid-1", "oid-2", "oid-3"]'
    assert parse_ids_file(content) == ["oid-1", "oid-2", "oid-3"]


def test_parse_ids_file_json_array_with_surrounding_whitespace() -> None:
    content = '\n  ["oid-1", "oid-2"]  \n'
    assert parse_ids_file(content) == ["oid-1", "oid-2"]


def test_parse_ids_file_json_array_skips_blank_entries() -> None:
    content = '["oid-1", "", "  ", "oid-2"]'
    assert parse_ids_file(content) == ["oid-1", "oid-2"]


def test_parse_ids_file_empty_content() -> None:
    assert parse_ids_file("") == []
    assert parse_ids_file("   \n  \n") == []


def test_merge_object_ids_combines_inline_and_file() -> None:
    inline = ["oid-1", "oid-2"]
    from_file = ["oid-3", "oid-4"]
    assert merge_object_ids(inline, from_file) == [
        "oid-1",
        "oid-2",
        "oid-3",
        "oid-4",
    ]


def test_merge_object_ids_dedups_preserving_first_seen_order() -> None:
    inline = ["oid-1", "oid-2"]
    from_file = ["oid-2", "oid-3", "oid-1"]
    assert merge_object_ids(inline, from_file) == ["oid-1", "oid-2", "oid-3"]


def test_merge_object_ids_strips_and_drops_empty() -> None:
    assert merge_object_ids(["  oid-1 ", "", "  "], ["oid-1"]) == ["oid-1"]
