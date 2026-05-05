# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the wire-format printable-ASCII sanitiser."""

from __future__ import annotations

import pytest

from terok_clearance.wire.sanitize import (
    DEFAULT_MAX_LEN,
    sanitize,
    sanitize_mapping,
)


class TestSanitize:
    """``sanitize`` collapses to printable ASCII and applies the length cap."""

    def test_empty_string_round_trips(self) -> None:
        assert sanitize("") == ""

    def test_plain_ascii_unchanged(self) -> None:
        assert sanitize("alpine-7-redis") == "alpine-7-redis"

    def test_full_printable_ascii_passes_through(self) -> None:
        """Every byte in [0x20, 0x7E] is preserved, including markup chars."""
        full = "".join(chr(c) for c in range(0x20, 0x7F))
        assert sanitize(full) == full

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("café", "caf "),
            ("münchen", "m nchen"),
            ("naïve", "na ve"),
        ],
    )
    def test_non_ascii_letters_become_spaces(self, raw: str, expected: str) -> None:
        """Non-ASCII codepoints — even legitimate Latin extended — drop to spaces."""
        assert sanitize(raw) == expected

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("line1\nline2", "line1 line2"),
            ("tab\there", "tab here"),
            ("car\rriage", "car riage"),
            ("null\x00byte", "null byte"),
            ("esc\x1bseq", "esc seq"),
            ("del\x7fchar", "del char"),
        ],
    )
    def test_control_chars_become_spaces(self, raw: str, expected: str) -> None:
        assert sanitize(raw) == expected

    def test_markup_chars_pass_unchanged(self) -> None:
        """``& < >`` are printable ASCII — central rule leaves them alone.

        Pango-specific escaping is layered in the desktop notifier just
        before D-Bus, not at the wire boundary.  Audit logs and TUI
        subscribers see the literal characters.
        """
        assert sanitize("<script>alert(1)</script>") == "<script>alert(1)</script>"
        assert sanitize("a & b") == "a & b"

    def test_rtlo_bidi_override_is_neutralised(self) -> None:
        """U+202E and friends become spaces — closes a homoglyph attack."""
        # The literal "evil‮.com" displays as "evil.live" on a bidi-aware terminal.
        assert sanitize("evil‮.com") == "evil .com"

    def test_length_cap_truncates_with_ascii_marker(self) -> None:
        """Truncation marker is three ASCII dots — itself printable ASCII."""
        out = sanitize("x" * 1000, max_len=10)
        assert out == "xxxxxxx..."
        assert len(out) == 10

    def test_value_at_exact_cap_passes_through(self) -> None:
        out = sanitize("x" * 10, max_len=10)
        assert out == "x" * 10
        assert "..." not in out

    def test_default_max_len_is_generous(self) -> None:
        """Realistic dossier values stay full-fidelity."""
        name = "warp-core/t42-feature-rebuild-2026-04"
        assert len(name) < DEFAULT_MAX_LEN
        assert sanitize(name) == name

    def test_combined_filters_apply(self) -> None:
        """Non-ASCII + control + length all apply when the input triggers each."""
        raw = "<bad>\nname\t" + "x" * 1000
        out = sanitize(raw, max_len=20)
        # No control bytes survive; markup chars are still printable; length capped.
        assert "\n" not in out
        assert "\t" not in out
        assert len(out) == 20
        assert out.endswith("...")


class TestSanitizeMapping:
    """``sanitize_mapping`` applies sanitisation to every value in a dict."""

    def test_sanitises_values_only(self) -> None:
        out = sanitize_mapping({"task": "<a>", "name": "b\nc"})
        # Markup chars survive (printable ASCII); newline becomes a space.
        assert out == {"task": "<a>", "name": "b c"}

    def test_keys_pass_through_unchanged(self) -> None:
        """Keys are internal identifiers — sanitiser is lenient on them."""
        out = sanitize_mapping({"<weird>": "v"})
        assert "<weird>" in out

    def test_empty_dict_round_trips(self) -> None:
        assert sanitize_mapping({}) == {}

    def test_max_len_threads_through(self) -> None:
        out = sanitize_mapping({"k": "x" * 1000}, max_len=5)
        assert out["k"] == "xx..."
