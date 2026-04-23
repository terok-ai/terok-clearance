# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for ``install_service`` — systemd user-unit installer."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from terok_clearance import _install
from terok_clearance._install import (
    UNIT_NAME,
    check_units_outdated,
    install_service,
    read_installed_unit,
    read_installed_unit_version,
)


class TestInstallService:
    """``install_service`` renders the unit template into the user systemd dir."""

    def test_writes_unit_with_bin_path_substituted(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        with patch.object(_install, "_daemon_reload"):
            dest = install_service(Path("/usr/local/bin/terok-clearance"))
        assert dest == tmp_path / "systemd" / "user" / UNIT_NAME
        body = dest.read_text()
        assert "{{BIN}}" not in body
        assert "/usr/local/bin/terok-clearance serve" in body

    def test_is_idempotent(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        with patch.object(_install, "_daemon_reload"):
            first = install_service(Path("/a/terok-clearance")).read_text()
            second = install_service(Path("/a/terok-clearance")).read_text()
        assert first == second

    def test_runs_daemon_reload(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        with patch.object(_install, "_daemon_reload") as reload:
            install_service(Path("/a/terok-clearance"))
        reload.assert_called_once()

    def test_daemon_reload_handles_missing_systemctl(self) -> None:
        """systemctl-missing hosts (e.g., CI containers) must not fail the install."""
        with patch.object(_install.shutil, "which", return_value=None):
            _install._daemon_reload()


class TestRenderExecStart:
    """Each argv token is quoted individually — spaces don't leak across boundaries."""

    def test_single_path_no_spaces_is_unquoted(self) -> None:
        assert (
            _install._render_exec_start(Path("/usr/bin/terok-clearance"))
            == "/usr/bin/terok-clearance"
        )

    def test_single_path_with_spaces_is_quoted(self) -> None:
        rendered = _install._render_exec_start(Path("/home/me/My Tools/terok-clearance"))
        assert rendered == '"/home/me/My Tools/terok-clearance"'

    def test_argv_list_quotes_each_token_individually(self) -> None:
        rendered = _install._render_exec_start(
            [Path("/home/me/My Py/python"), "-m", "terok_clearance._cli"]
        )
        assert rendered == '"/home/me/My Py/python" -m terok_clearance._cli'

    def test_control_characters_are_refused(self) -> None:
        with pytest.raises(ValueError):
            _install._render_exec_start(Path("/a/terok-clearance\nRestart=never"))


class TestReadInstalledUnit:
    """``read_installed_unit`` returns the installed text, or None when absent."""

    def test_returns_text_when_present(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        with patch.object(_install, "_daemon_reload"):
            install_service(Path("/a/terok-clearance"))
        text = read_installed_unit()
        assert text is not None
        assert "/a/terok-clearance serve" in text

    def test_returns_none_when_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        assert read_installed_unit() is None


class TestUnitVersion:
    """Version marker lets sickbay tell fresh installs from stale ones."""

    def test_rendered_unit_carries_current_version(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        with patch.object(_install, "_daemon_reload"):
            install_service(Path("/a/terok-clearance"))
        assert read_installed_unit_version() == _install._UNIT_VERSION

    def test_read_version_returns_none_without_marker(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A pre-varlink Shield1 hub would have no marker line at all."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        unit_path = tmp_path / "systemd" / "user" / UNIT_NAME
        unit_path.parent.mkdir(parents=True)
        unit_path.write_text("[Unit]\nDescription=legacy\n[Service]\nExecStart=/x serve\n")
        assert read_installed_unit_version() is None

    def test_check_outdated_silent_on_fresh_install(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        with patch.object(_install, "_daemon_reload"):
            install_service(Path("/a/terok-clearance"))
        assert check_units_outdated() is None

    def test_check_outdated_silent_when_absent(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """No unit installed is headless-host shape, not a drift warning."""
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        assert check_units_outdated() is None

    def test_check_outdated_flags_unversioned_unit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        unit_path = tmp_path / "systemd" / "user" / UNIT_NAME
        unit_path.parent.mkdir(parents=True)
        unit_path.write_text("[Unit]\n[Service]\nExecStart=/x\n")
        msg = check_units_outdated()
        assert msg is not None
        assert "unversioned" in msg
        assert "terok setup" in msg

    def test_check_outdated_flags_older_version(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
        unit_path = tmp_path / "systemd" / "user" / UNIT_NAME
        unit_path.parent.mkdir(parents=True)
        unit_path.write_text(f"# terok-dbus-version: {_install._UNIT_VERSION - 1}\n[Service]\n")
        msg = check_units_outdated()
        assert msg is not None
        assert f"v{_install._UNIT_VERSION - 1}" in msg
        assert f"v{_install._UNIT_VERSION}" in msg
