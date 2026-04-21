# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Unit tests for the podman-backed container-name resolver."""

from __future__ import annotations

import subprocess
from unittest import mock

from terok_dbus._podman import PodmanContainerNameResolver


def _fake_proc(returncode: int, stdout: str = "", stderr: str = "") -> subprocess.CompletedProcess:
    """Shape one ``subprocess.run`` result for the resolver to consume."""
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


class TestPodmanContainerNameResolver:
    """``PodmanContainerNameResolver`` returns the container Name, caches, soft-fails."""

    def test_returns_name_from_podman_inspect(self) -> None:
        """Happy path: podman returns a name and the resolver passes it through."""
        with (
            mock.patch("terok_dbus._podman.shutil.which", return_value="/usr/bin/podman"),
            mock.patch(
                "terok_dbus._podman.subprocess.run",
                return_value=_fake_proc(0, stdout="my-task\n"),
            ),
        ):
            resolver = PodmanContainerNameResolver()
            assert resolver("abc123") == "my-task"

    def test_empty_id_returns_empty(self) -> None:
        """An empty container ID never reaches podman."""
        with mock.patch("terok_dbus._podman.subprocess.run") as run:
            resolver = PodmanContainerNameResolver()
            assert resolver("") == ""
            run.assert_not_called()

    def test_caches_lookups(self) -> None:
        """Repeat calls for the same ID don't re-invoke podman."""
        with (
            mock.patch("terok_dbus._podman.shutil.which", return_value="/usr/bin/podman"),
            mock.patch(
                "terok_dbus._podman.subprocess.run",
                return_value=_fake_proc(0, stdout="my-task\n"),
            ) as run,
        ):
            resolver = PodmanContainerNameResolver()
            assert resolver("abc123") == "my-task"
            assert resolver("abc123") == "my-task"
            assert run.call_count == 1

    def test_returns_empty_when_podman_missing(self) -> None:
        """No podman on PATH → empty string, caller falls back to the ID."""
        with mock.patch("terok_dbus._podman.shutil.which", return_value=None):
            assert PodmanContainerNameResolver()("abc123") == ""

    def test_returns_empty_on_inspect_nonzero(self) -> None:
        """podman inspect failure (unknown ID) → empty string."""
        with (
            mock.patch("terok_dbus._podman.shutil.which", return_value="/usr/bin/podman"),
            mock.patch(
                "terok_dbus._podman.subprocess.run",
                return_value=_fake_proc(1, stderr="no such container"),
            ),
        ):
            assert PodmanContainerNameResolver()("abc123") == ""

    def test_returns_empty_on_timeout(self) -> None:
        """podman hung → empty string."""
        with (
            mock.patch("terok_dbus._podman.shutil.which", return_value="/usr/bin/podman"),
            mock.patch(
                "terok_dbus._podman.subprocess.run",
                side_effect=subprocess.TimeoutExpired(cmd="podman", timeout=5),
            ),
        ):
            assert PodmanContainerNameResolver()("abc123") == ""

    def test_returns_empty_on_oserror(self) -> None:
        """Subprocess raises OSError (e.g. binary missing mid-run) → empty string."""
        with (
            mock.patch("terok_dbus._podman.shutil.which", return_value="/usr/bin/podman"),
            mock.patch("terok_dbus._podman.subprocess.run", side_effect=OSError("no such file")),
        ):
            assert PodmanContainerNameResolver()("abc123") == ""

    def test_argv_uses_dash_dash_separator(self) -> None:
        """``--`` precedes the container argument to guard against leading-dash IDs."""
        with (
            mock.patch("terok_dbus._podman.shutil.which", return_value="/usr/bin/podman"),
            mock.patch(
                "terok_dbus._podman.subprocess.run",
                return_value=_fake_proc(0, stdout="t\n"),
            ) as run,
        ):
            PodmanContainerNameResolver()("abc123")
        argv = run.call_args.args[0]
        assert "--" in argv
        assert argv.index("abc123") > argv.index("--")
