# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Wire-format string invariant: printable ASCII only, length-capped.

Every string field that crosses the reader → hub socket originates,
directly or indirectly, in container-controlled bytes — a crafted DNS
name, a forged hostname header, an OCI annotation copied verbatim from
an untrusted runtime configuration.  The hub fans those values out to
multiple consumers (desktop notifier, Textual TUIs, future audit /
forensic listeners), each with its own rendering quirks: Pango markup,
ANSI terminal escapes, JSON log aggregators.  Defining a single
catch-everything rule on the receive side spares every consumer from
re-implementing its own escape pass — and stops a third-party
subscriber from inheriting whatever footgun a consumer-specific
sanitiser missed.

The rule is deliberately the smallest one that holds the contract:

* Bytes inside ``[\\x20, \\x7E]`` (printable ASCII, including space)
  pass through verbatim.
* Anything else — control chars, ``\\n``, ``\\t``, NULs, every
  non-ASCII codepoint, RTLO/LRO bidi overrides, NBSP, the lot — is
  replaced with a single space.  Position is preserved so adjacent
  printable characters stay readable.
* Strings longer than ``max_len`` are truncated and end with a
  three-dot ``...`` marker.  ``…`` (U+2026) is itself outside the
  printable-ASCII window, so it can't double as a truncation indicator
  under our own rule.

The ASCII restriction loses non-ASCII project / task / hostname
display fidelity (``café`` becomes ``caf ``), but the wire-format
docs name that as a deliberate tradeoff: simpler invariant, smaller
attack surface, parser-robustness for free.  Sinks that want
markup-specific escaping (``& < >`` for Pango) layer it on top — the
chars are still printable ASCII at this stage so the layered escape
remains well-defined.
"""

from __future__ import annotations

#: Default per-value length cap.  Generous enough for any realistic
#: hostname, task name, or dossier value while staying inside
#: gnome-shell's two-line popup body shape.  Override per-call when a
#: specific surface (notification title, compact label) wants tighter
#: bounds.
DEFAULT_MAX_LEN = 256

#: Inclusive lower / upper bounds for the printable-ASCII keep set.
#: Space (``0x20``) and tilde (``0x7E``) are both in-bounds; DEL
#: (``0x7F``) and everything above are not.
_PRINTABLE_LO = 0x20
_PRINTABLE_HI = 0x7E

#: Truncation indicator used at the end of capped strings.  Three
#: dots rather than ``…`` because the latter is not printable ASCII
#: under our own rule and would have to be sanitised away by the very
#: function that produced it.
_TRUNCATION_MARKER = "..."


def sanitize(value: str, *, max_len: int = DEFAULT_MAX_LEN) -> str:
    """Return *value* coerced to printable ASCII, capped at *max_len*.

    Non-printable / non-ASCII bytes become a single space; the
    resulting string is truncated to ``max_len`` characters with a
    trailing ``...`` if the cap actually fired.  Empty input
    round-trips unchanged.

    Args:
        value: Raw string from a wire payload — assumed UTF-8 already
            decoded by the JSON parser, but otherwise of unknown
            provenance.
        max_len: Hard ceiling on the returned length, including the
            truncation marker when present.  ``DEFAULT_MAX_LEN`` is
            wide enough for normal task/hostname values; tighter
            values suit titles and compact labels.

    Returns:
        A string composed entirely of ``[\\x20-\\x7E]``, no longer
        than ``max_len`` characters.
    """
    if not value:
        return ""
    cleaned = "".join(ch if _PRINTABLE_LO <= ord(ch) <= _PRINTABLE_HI else " " for ch in value)
    if len(cleaned) <= max_len:
        return cleaned
    return cleaned[: max_len - len(_TRUNCATION_MARKER)] + _TRUNCATION_MARKER


def sanitize_mapping(mapping: dict[str, str], *, max_len: int = DEFAULT_MAX_LEN) -> dict[str, str]:
    """Apply [`sanitize`][terok_clearance.wire.sanitize.sanitize] to every value in *mapping*.

    Keys flow through unchanged — they are internal contract identifiers
    (``project``, ``task``, ``name``, …), not user-visible strings, so
    a typo'd or attacker-injected key still surfaces somewhere a
    debugging human can spot it rather than being silently rewritten.
    """
    return {k: sanitize(v, max_len=max_len) for k, v in mapping.items()}
