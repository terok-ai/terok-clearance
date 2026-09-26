# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Shield companions retain installation precedence over live PATH lookup."""

import os
import sys
from pathlib import Path

import pytest

from terok_clearance.verdict.server import find_shield_binary


def _executable(directory: Path) -> Path:
    """Create a test companion without executing it."""
    directory.mkdir(parents=True, exist_ok=True)
    binary = directory / "terok-shield"
    binary.write_text("fixture", encoding="utf-8")
    binary.chmod(0o700)
    return binary


def test_installation_companion_precedes_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A matching installation wins even when another Shield occurs first in PATH."""
    sibling = _executable(tmp_path / "venv")
    other = _executable(tmp_path / "path")
    monkeypatch.setattr(sys, "executable", str(sibling.parent / "python"))
    monkeypatch.setenv("PATH", str(other.parent))
    assert find_shield_binary() == str(sibling)


def test_fallback_uses_live_absolute_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Reject cwd-based lookup and observe PATH changes at each discovery."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "python"))
    _executable(tmp_path / "relative")
    first = _executable(tmp_path / "first")
    second = _executable(tmp_path / "second")
    monkeypatch.setenv("PATH", os.pathsep.join(("relative", str(first.parent))))
    assert find_shield_binary() == str(first)
    monkeypatch.setenv("PATH", str(second.parent))
    assert find_shield_binary() == str(second)


def test_nonexecutable_companion_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An unusable sibling does not hide the executable available on PATH."""
    sibling = _executable(tmp_path / "venv")
    sibling.chmod(0o600)
    fallback = _executable(tmp_path / "path")
    monkeypatch.setattr(sys, "executable", str(sibling.parent / "python"))
    monkeypatch.setenv("PATH", str(fallback.parent))
    assert find_shield_binary() == str(fallback)
