# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``install_service`` — systemd user-unit installer."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from terok_dbus import _install
from terok_dbus._install import (
    UNIT_NAME,
    extract_baked_state_dir,
    install_service,
    read_installed_unit,
)


class TestInstallService:
    """``install_service`` renders the unit template into the user systemd dir."""

    def test_writes_unit_with_bin_path_substituted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.delenv("TEROK_SHIELD_STATE_DIR", raising=False)
        with patch.object(_install, "_daemon_reload"):
            dest = install_service("/usr/local/bin/terok-dbus")
        assert dest == tmp_path / "systemd" / "user" / UNIT_NAME
        body = dest.read_text()
        assert "{{BIN}}" not in body
        assert "/usr/local/bin/terok-dbus serve" in body

    def test_is_idempotent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.delenv("TEROK_SHIELD_STATE_DIR", raising=False)
        with patch.object(_install, "_daemon_reload"):
            first = install_service("/a/terok-dbus").read_text()
            second = install_service("/a/terok-dbus").read_text()
        assert first == second

    def test_runs_daemon_reload(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.delenv("TEROK_SHIELD_STATE_DIR", raising=False)
        with patch.object(_install, "_daemon_reload") as reload:
            install_service("/a/terok-dbus")
        reload.assert_called_once()

    def test_daemon_reload_handles_missing_systemctl(self) -> None:
        """systemctl-missing hosts (e.g., CI containers) must not fail the install."""
        with patch.object(_install.shutil, "which", return_value=None):
            _install._daemon_reload()


class TestStateDirEnvBaking:
    """``TEROK_SHIELD_STATE_DIR`` is baked into the unit only when set at install time."""

    def test_absent_env_leaves_no_environment_line(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.delenv("TEROK_SHIELD_STATE_DIR", raising=False)
        with patch.object(_install, "_daemon_reload"):
            dest = install_service("/a/terok-dbus")
        body = dest.read_text()
        assert "Environment=TEROK_SHIELD_STATE_DIR=" not in body

    def test_present_env_bakes_environment_line(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setenv("TEROK_SHIELD_STATE_DIR", "/custom/state")
        with patch.object(_install, "_daemon_reload"):
            dest = install_service("/a/terok-dbus")
        body = dest.read_text()
        assert "Environment=TEROK_SHIELD_STATE_DIR=/custom/state" in body

    def test_baked_env_lands_after_execstart(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Env line sits right after ExecStart so the unit reads top-down."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.setenv("TEROK_SHIELD_STATE_DIR", "/custom/state")
        with patch.object(_install, "_daemon_reload"):
            dest = install_service("/a/terok-dbus")
        lines = dest.read_text().splitlines()
        exec_idx = next(i for i, line in enumerate(lines) if line.startswith("ExecStart="))
        env_idx = next(
            i for i, line in enumerate(lines) if "Environment=TEROK_SHIELD_STATE_DIR=" in line
        )
        assert env_idx == exec_idx + 2


class TestReadInstalledUnit:
    """``read_installed_unit`` returns the installed text, or None when absent."""

    def test_returns_text_when_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        monkeypatch.delenv("TEROK_SHIELD_STATE_DIR", raising=False)
        with patch.object(_install, "_daemon_reload"):
            install_service("/a/terok-dbus")
        text = read_installed_unit()
        assert text is not None
        assert "/a/terok-dbus serve" in text

    def test_returns_none_when_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        assert read_installed_unit() is None


class TestExtractBakedStateDir:
    """``extract_baked_state_dir`` pulls the baked value from unit text."""

    def test_returns_value_when_present(self) -> None:
        unit = "[Service]\nExecStart=/a/terok-dbus serve\nEnvironment=TEROK_SHIELD_STATE_DIR=/foo\n"
        assert extract_baked_state_dir(unit) == "/foo"

    def test_returns_none_when_absent(self) -> None:
        unit = "[Service]\nExecStart=/a/terok-dbus serve\n"
        assert extract_baked_state_dir(unit) is None

    def test_tolerates_surrounding_whitespace(self) -> None:
        unit = "[Service]\n   Environment=TEROK_SHIELD_STATE_DIR=/bar   \n"
        assert extract_baked_state_dir(unit) == "/bar"
