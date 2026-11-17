# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""The ``clearance:`` section schema, and its agreement with the hub that reads the section."""

import pytest
from pydantic import ValidationError

from terok_clearance import RawClearanceSection
from terok_clearance.domain.verdict_ttl import DEFAULT_VERDICT_TTL, parse_verdict_ttl
from terok_clearance.hub import server as hub

_USABLE_TTLS = ["24h", "30m", "7d", "60s", "1.5h", "3600", 3600, 1.5, "never", "none", 0, 0.0]
_UNUSABLE_TTLS = ["abc", "5 minutes", "", True, False, [3600], {"hours": 24}]


def _hub_reads(monkeypatch: pytest.MonkeyPatch, section: dict) -> float | None:
    """The TTL the hub takes from a ``clearance:`` section of ``config.yml``."""
    monkeypatch.setattr(
        "terok_util.paths.read_config_section",
        lambda name: section if name == "clearance" else {},
    )
    return hub._read_verdict_ttl_from_config()


def test_an_empty_section_leaves_the_ttl_to_its_default() -> None:
    """Nothing set means the hub's 24 hours, which the schema spells as ``None``."""
    section = RawClearanceSection()

    assert section.verdict_ttl is None
    assert parse_verdict_ttl(section.verdict_ttl) == DEFAULT_VERDICT_TTL


@pytest.mark.parametrize("ttl", _USABLE_TTLS)
def test_a_ttl_the_schema_accepts_is_the_ttl_the_hub_applies(
    monkeypatch: pytest.MonkeyPatch, ttl: object
) -> None:
    """A composed schema and the hub read the same YAML value to the same lifetime."""
    section = RawClearanceSection.model_validate({"verdict_ttl": ttl})

    assert section.verdict_ttl == str(ttl)
    assert parse_verdict_ttl(section.verdict_ttl) == _hub_reads(monkeypatch, {"verdict_ttl": ttl})


@pytest.mark.parametrize("ttl", _UNUSABLE_TTLS)
def test_a_ttl_the_hub_would_drop_fails_validation(
    monkeypatch: pytest.MonkeyPatch, ttl: object
) -> None:
    """The hub falls back to its default on such a value, so the schema refuses it."""
    with pytest.raises(ValidationError, match="verdict_ttl"):
        RawClearanceSection.model_validate({"verdict_ttl": ttl})

    assert _hub_reads(monkeypatch, {"verdict_ttl": ttl}) == DEFAULT_VERDICT_TTL


def test_an_unknown_key_fails_validation() -> None:
    """The section is strict on its own keys, so a typo shows at load."""
    with pytest.raises(ValidationError, match="verdict_tll"):
        RawClearanceSection.model_validate({"verdict_tll": "24h"})


def test_the_schema_declares_the_key_the_hub_reads() -> None:
    """The hub looks its key up by name, outside the model."""
    assert hub.VERDICT_TTL_CONFIG_KEY in RawClearanceSection.model_fields


def test_every_option_carries_a_description() -> None:
    """Composing packages render the description as the option's help."""
    assert all(field.description for field in RawClearanceSection.model_fields.values())
