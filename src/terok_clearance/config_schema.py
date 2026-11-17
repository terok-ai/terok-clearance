# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Pydantic schema for the clearance-owned slice of the shared ``config.yml``.

The ecosystem shares one ``~/.config/terok/config.yml``.  Each package
owns the schema of the sections it reads, and a package above composes
the whole file from those schemas (terok-sandbox describes the
arrangement in its own ``config_schema``).  This module is clearance's
part: the ``clearance:`` section, which the hub reads.

The section is strict on its own keys (``extra="forbid"``), as every
owned section is.  The hub does not load this module.  It parses the
value with [`parse_verdict_ttl`][terok_clearance.domain.verdict_ttl.parse_verdict_ttl],
the function this schema validates with, so the two cannot disagree and
the hub stays free of pydantic.
"""

from typing import Annotated, Any

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field

from terok_clearance.domain.verdict_ttl import parse_verdict_ttl


def _parseable_verdict_ttl(value: Any) -> Any:
    """Take a YAML number as seconds, and refuse a value the hub could not parse."""
    if value is None:
        return None
    parse_verdict_ttl(value)
    return str(value)


class RawClearanceSection(BaseModel):
    """The ``clearance:`` section — what the clearance hub reads."""

    model_config = ConfigDict(extra="forbid")

    verdict_ttl: Annotated[str | None, BeforeValidator(_parseable_verdict_ttl)] = Field(
        default=None,
        description=(
            "How long an unanswered Allow/Deny prompt can still be answered: seconds, or a"
            " count with a unit, such as ``30m``, ``24h`` or ``7d``.  ``never`` keeps a"
            " prompt until its container ends.  Unset means 24 hours"
        ),
    )
