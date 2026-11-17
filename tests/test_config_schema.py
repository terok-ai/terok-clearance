# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""The ``clearance:`` section schema, and its agreement with the hub that reads the section.

Both sides read one real ``config.yml``.  The hub goes through
terok-util's layered reader, which hands it every value as text.  A
composing package validates the parsed YAML, where a number is still a
number.  The tests check that the two reach the same lifetime.
"""

from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import ValidationError
from terok_util import paths
from terok_util.config_stack import load_yaml_scope

from terok_clearance import RawClearanceSection, parse_verdict_ttl
from terok_clearance.domain.verdict_ttl import DEFAULT_VERDICT_TTL
from terok_clearance.hub import server as hub

_USABLE_TTLS = ["24h", "30m", "7d", "60s", "1.5h", "3600", "'3600'", "1.5", "never", "none"]
_USABLE_TTLS += ["off", "0", "0.0"]
_UNUSABLE_TTLS = ["abc", "5 minutes", "''", "true", "[3600]", "{hours: 24}"]


@pytest.fixture
def config_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """A ``config.yml`` that is the only config layer while the test runs."""
    path = tmp_path / "config.yml"
    monkeypatch.setenv("TEROK_CONFIG_FILE", str(path))
    paths._reset_config_caches_for_tests()
    yield path
    paths._reset_config_caches_for_tests()


def _set_ttl(config_file: Path, yaml_value: str) -> dict:
    """Write ``clearance.verdict_ttl`` as *yaml_value* and return the parsed section."""
    config_file.write_text(f"clearance:\n  verdict_ttl: {yaml_value}\n", encoding="utf-8")
    return load_yaml_scope("test", config_file).data["clearance"]


def test_an_empty_section_leaves_the_ttl_to_its_default() -> None:
    """Nothing set means the hub's 24 hours, which the schema spells as ``None``."""
    section = RawClearanceSection()

    assert section.verdict_ttl is None
    assert parse_verdict_ttl(section.verdict_ttl) == DEFAULT_VERDICT_TTL


@pytest.mark.parametrize("ttl", _USABLE_TTLS)
def test_a_ttl_the_schema_accepts_is_the_ttl_the_hub_applies(config_file: Path, ttl: str) -> None:
    """A composed schema and the hub read the same file to the same lifetime."""
    section = RawClearanceSection.model_validate(_set_ttl(config_file, ttl))

    assert isinstance(section.verdict_ttl, str)
    assert parse_verdict_ttl(section.verdict_ttl) == hub._read_verdict_ttl_from_config()


@pytest.mark.parametrize("ttl", _UNUSABLE_TTLS)
def test_a_ttl_the_hub_would_drop_fails_validation(config_file: Path, ttl: str) -> None:
    """The hub falls back to its default on such a value, so the schema refuses it."""
    raw = _set_ttl(config_file, ttl)

    with pytest.raises(ValidationError, match="verdict_ttl"):
        RawClearanceSection.model_validate(raw)
    assert hub._read_verdict_ttl_from_config() == DEFAULT_VERDICT_TTL


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
