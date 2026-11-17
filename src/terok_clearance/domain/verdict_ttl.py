# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""The grammar of ``clearance.verdict_ttl``: how long an unanswered verdict stays pending.

One definition serves both readers of the key.  The hub parses the value
that it finds in ``config.yml``, and
[`RawClearanceSection`][terok_clearance.config_schema.RawClearanceSection]
validates the value with the same function, so what a composed schema
accepts is what the hub honours.  The two see the value differently:
terok-util's layered reader hands the hub every value as text, while a
schema validates the parsed YAML, where a number is still a number.  The
parser therefore takes both.

The module imports nothing beyond the standard library, because each
reader must stay free of the other's dependencies: the hub of pydantic,
the schema of the varlink stack.
"""

import re

#: Default pending-verdict TTL: 24 hours.  Long enough for overnight
#: tasks where the operator reviews in the morning; short enough that
#: a long-running container doesn't accumulate unbounded state.
#: ``never`` disables expiry (backward-compatible behaviour).
DEFAULT_VERDICT_TTL = 24 * 3600.0

_TTL_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*([smhd])?$", re.IGNORECASE)
_TTL_UNITS = {"s": 1.0, "m": 60.0, "h": 3600.0, "d": 86400.0, None: 1.0}


def parse_verdict_ttl(value: str | float | None) -> float | None:
    """Parse a TTL into seconds, or ``None`` for "no expiry".

    Accepts plain seconds (``3600``, as text or as the number YAML makes
    of it), unit-suffixed (``24h``, ``30m``, ``7d``), and the special
    values ``never`` / ``none`` (both meaning "no expiry").  A zero
    duration (``0``, ``0.0``, ``0s``) also means "no expiry": a pending
    verdict that expires at once is never what an operator asks for.
    ``None`` input returns the default (24h).
    Raises ``ValueError`` on malformed input.
    """
    if value is None:
        return DEFAULT_VERDICT_TTL
    v = str(value).strip().lower()
    if v in ("never", "none", "off", "disabled"):
        return None
    m = _TTL_RE.fullmatch(v)
    if not m:
        raise ValueError(f"invalid TTL {value!r} (expected e.g. '3600', '24h', '30m', 'never')")
    num = float(m.group(1))
    unit = m.group(2)
    return num * _TTL_UNITS[unit] or None
