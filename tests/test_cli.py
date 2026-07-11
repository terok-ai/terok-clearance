# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the terok-clearance CLI — subcommand parsing and dispatch."""

import argparse
from unittest.mock import AsyncMock, patch

import pytest
from terok_util import CommandDef, CommandTree

from terok_clearance.cli.main import main
from terok_clearance.commands import COMMANDS


def _parse(argv: list[str]) -> argparse.Namespace:
    """Wire the registry the way ``main`` does and parse *argv*."""
    parser = argparse.ArgumentParser(prog="terok-clearance-hub")
    CommandTree(COMMANDS).wire(parser, argv=argv)
    return parser.parse_args(argv)


class TestNotifyParser:
    """Argument parsing for the ``notify`` subcommand."""

    def test_summary_required(self):
        args = _parse(["notify", "Hello"])
        assert args._cmd.name == "notify"
        assert args.summary == "Hello"
        assert args.body == ""
        assert args.timeout == -1

    def test_summary_and_body(self):
        args = _parse(["notify", "Hello", "World"])
        assert args.summary == "Hello"
        assert args.body == "World"

    def test_timeout_flag(self):
        args = _parse(["notify", "-t", "5000", "Hello"])
        assert args.timeout == 5000

    def test_timeout_long_flag(self):
        args = _parse(["notify", "--timeout", "3000", "Hello"])
        assert args.timeout == 3000


class TestServeParser:
    """Argument parsing for the ``serve`` subcommand."""

    def test_parses_with_no_args(self) -> None:
        args = _parse(["serve"])
        assert args._cmd.name == "serve"


class TestNoSubcommand:
    """Bare ``terok-clearance-hub`` with no subcommand."""

    def test_exits_with_code_2(self):
        with patch("sys.argv", ["terok-clearance-hub"]):
            with pytest.raises(SystemExit, match="2"):
                main()


class TestNotifyDispatch:
    """Dispatch tests for ``terok-clearance-hub notify``."""

    def test_notify_sends_notification(self):
        mock_notifier = AsyncMock()
        mock_notifier.notify.return_value = 42

        with (
            patch(
                "terok_clearance.notifications.factory.create_notifier", new_callable=AsyncMock
            ) as mock_factory,
            patch("sys.argv", ["terok-clearance-hub", "notify", "Test", "Body"]),
        ):
            mock_factory.return_value = mock_notifier
            main()
            mock_notifier.notify.assert_awaited_once_with("Test", "Body", timeout_ms=-1)
            mock_notifier.disconnect.assert_awaited_once()


class TestKeyboardInterrupt:
    """Handler raises KeyboardInterrupt → exit code 130."""

    def test_keyboard_interrupt_exits_130(self):
        mock_handler = AsyncMock(side_effect=KeyboardInterrupt)
        notify_args = next(c for c in COMMANDS if c.name == "notify").resolve().args
        mock_commands = tuple(
            CommandDef(name=cmd.name, handler=mock_handler, args=notify_args)
            if cmd.name == "notify"
            else cmd
            for cmd in COMMANDS
        )

        with (
            patch("terok_clearance.cli.main.COMMANDS", mock_commands),
            patch("sys.argv", ["terok-clearance-hub", "notify", "Hi"]),
        ):
            with pytest.raises(SystemExit, match="130"):
                main()


class TestServeDispatch:
    """Dispatch tests for ``terok-clearance-hub serve``."""

    def test_serve_dispatches_to_handler(self) -> None:
        mock_handler = AsyncMock()
        mock_commands = tuple(
            CommandDef(name=cmd.name, handler=mock_handler) if cmd.name == "serve" else cmd
            for cmd in COMMANDS
        )

        with (
            patch("terok_clearance.cli.main.COMMANDS", mock_commands),
            patch("sys.argv", ["terok-clearance-hub", "serve"]),
        ):
            main()
            mock_handler.assert_awaited_once()
