# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the terminal clearance tool and its COMMANDS registry entry."""

from unittest.mock import Mock

import pytest

from terok_dbus._callback import Notification
from terok_dbus._clearance import _TerminalClearance
from terok_dbus._registry import COMMANDS


class TestTerminalClearance:
    """Tests for _TerminalClearance input handling."""

    def test_on_notify_new_blocked(self, capsys) -> None:
        """A notification with actions is added to pending and printed."""
        tc = _TerminalClearance()
        n = Notification(
            nid=1,
            summary="Blocked: foo.com:443",
            body="Container: c1",
            actions=[("accept", "Allow")],
            replaces_id=0,
            timeout_ms=0,
        )
        tc._on_notify(n)
        assert 1 in tc._pending
        assert "BLOCKED" in capsys.readouterr().out

    def test_on_notify_verdict_applied(self, capsys) -> None:
        """A replaces_id notification removes from pending and prints result."""
        tc = _TerminalClearance()
        tc._pending[1] = Notification(
            nid=1, summary="x", body="", actions=[], replaces_id=0, timeout_ms=0
        )
        n = Notification(
            nid=1,
            summary="Allowed: foo.com",
            body="Container: c1",
            actions=[],
            replaces_id=1,
            timeout_ms=5000,
        )
        tc._on_notify(n)
        assert 1 not in tc._pending
        assert "Allowed" in capsys.readouterr().out

    def test_handle_allow(self) -> None:
        """'a <N>' invokes the callback with 'accept'."""
        tc = _TerminalClearance()
        cb = Mock()
        tc._notifier._callbacks[1] = cb
        tc._pending[1] = Notification(
            nid=1, summary="x", body="", actions=[], replaces_id=0, timeout_ms=0
        )
        tc._handle_input("a 1")
        cb.assert_called_once_with("accept")

    def test_handle_deny(self) -> None:
        """'d <N>' invokes the callback with 'deny'."""
        tc = _TerminalClearance()
        cb = Mock()
        tc._notifier._callbacks[2] = cb
        tc._pending[2] = Notification(
            nid=2, summary="x", body="", actions=[], replaces_id=0, timeout_ms=0
        )
        tc._handle_input("d 2")
        cb.assert_called_once_with("deny")

    def test_handle_unknown_nid(self, capsys) -> None:
        """Attempting to allow a non-existent request prints an error."""
        tc = _TerminalClearance()
        tc._handle_input("a 99")
        assert "No pending" in capsys.readouterr().out

    def test_handle_list(self, capsys) -> None:
        """'l' lists pending requests."""
        tc = _TerminalClearance()
        tc._pending[1] = Notification(
            nid=1, summary="foo", body="bar", actions=[], replaces_id=0, timeout_ms=0
        )
        tc._handle_input("l")
        out = capsys.readouterr().out
        assert "[1]" in out
        assert "foo" in out

    def test_handle_list_empty(self, capsys) -> None:
        """'l' with no pending shows a message."""
        tc = _TerminalClearance()
        tc._handle_input("l")
        assert "no pending" in capsys.readouterr().out

    def test_handle_help(self, capsys) -> None:
        """'h' shows help text."""
        tc = _TerminalClearance()
        tc._handle_input("h")
        out = capsys.readouterr().out
        assert "allow" in out
        assert "deny" in out

    def test_handle_quit_raises(self) -> None:
        """'q' raises KeyboardInterrupt to exit."""
        tc = _TerminalClearance()
        with pytest.raises(KeyboardInterrupt):
            tc._handle_input("q")

    def test_handle_unknown_command(self, capsys) -> None:
        """Unknown commands print an error."""
        tc = _TerminalClearance()
        tc._handle_input("xyz")
        assert "Unknown" in capsys.readouterr().out


class TestClearanceRegistryEntry:
    """The clearance command must be in the COMMANDS registry."""

    def test_clearance_in_commands(self) -> None:
        """COMMANDS includes a 'clearance' entry."""
        names = {cmd.name for cmd in COMMANDS}
        assert "clearance" in names

    def test_clearance_has_handler(self) -> None:
        """The clearance CommandDef has a handler."""
        cmd = next(c for c in COMMANDS if c.name == "clearance")
        assert cmd.handler is not None

    def test_clearance_has_no_args(self) -> None:
        """The clearance command takes no CLI arguments."""
        cmd = next(c for c in COMMANDS if c.name == "clearance")
        assert cmd.args == ()
