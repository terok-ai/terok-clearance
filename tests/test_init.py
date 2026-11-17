# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the public API surface of terok_clearance."""

import ast
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import terok_clearance
from terok_clearance import create_notifier
from terok_clearance.notifications.desktop import DbusNotifier
from terok_clearance.notifications.factory import NullNotifier
from terok_clearance.notifications.protocol import Notifier


class TestPublicApi:
    """Verify that __all__ matches the actual public API."""

    def test_all_exports(self):
        expected = {
            "ALL_NOTIFY_CATEGORIES",
            "COMMANDS",
            "CallbackNotifier",
            "ClearanceClient",
            "set_container_mute",
            "container_mutes",
            "ClearanceEvent",
            "ClearanceHub",
            "EventSubscriber",
            "MultiSocketSubscriber",
            "NOTIFY_BLOCKED",
            "NOTIFY_VERDICT",
            "Notification",
            "RawClearanceSection",
            "VerdictClient",
            "VerdictServer",
            "create_notifier",
            "default_clearance_socket_path",
            "parse_verdict_ttl",
        }
        assert set(terok_clearance.__all__) == expected

    def test_all_matches_lazy_map(self):
        """``__all__`` and the lazy re-export map stay in lockstep."""
        assert set(terok_clearance.__all__) == set(terok_clearance._LAZY)

    def test_type_checking_block_mirrors_lazy_map(self):
        """Each lazy name is imported under ``TYPE_CHECKING`` from the module ``_LAZY`` names.

        mypy and IDEs see the barrel only through that block: a name missing
        there still imports at runtime but reads as ``object`` to a type
        checker, which is how a public symbol silently loses its types.
        """
        tree = ast.parse(Path(terok_clearance.__file__).read_text(encoding="utf-8"))
        guarded = next(
            node
            for node in tree.body
            if isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == "TYPE_CHECKING"
        )
        imported = {
            alias.asname or alias.name: node.module
            for node in guarded.body
            if isinstance(node, ast.ImportFrom)
            for alias in node.names
        }
        assert imported == terok_clearance._LAZY

    def test_all_names_resolve(self):
        """Every ``__all__`` name imports lazily via ``__getattr__``."""
        for name in terok_clearance.__all__:
            assert getattr(terok_clearance, name) is not None

    def test_notifier_is_protocol(self):
        assert isinstance(NullNotifier(), Notifier)


class TestCreateNotifier:
    """Factory function tests."""

    async def test_returns_dbus_notifier_when_bus_available(self):
        mock_bus = MagicMock()
        mock_bus.connect = AsyncMock(return_value=mock_bus)
        mock_bus.introspect = AsyncMock(return_value=MagicMock())
        proxy = MagicMock()
        proxy.get_interface.return_value = MagicMock()
        mock_bus.get_proxy_object.return_value = proxy
        # ``connect()`` now sends an AddMatch via ``bus.call`` to wire
        # up the raw signal handler — return a stub method-return so
        # the AddMatch awaitable resolves.
        method_return = MagicMock()
        method_return.message_type = MagicMock()
        method_return.message_type.__eq__ = lambda _self, _other: False
        mock_bus.call = AsyncMock(return_value=method_return)
        mock_bus._name_owners = {}

        with patch("terok_clearance.notifications.desktop.MessageBus", return_value=mock_bus):
            notifier = await create_notifier("test")
            assert isinstance(notifier, DbusNotifier)

    async def test_returns_null_notifier_on_connection_failure(self):
        mock_bus = MagicMock()
        mock_bus.connect = AsyncMock(side_effect=OSError("no bus"))

        with patch("terok_clearance.notifications.desktop.MessageBus", return_value=mock_bus):
            notifier = await create_notifier()
            assert isinstance(notifier, NullNotifier)

    async def test_returns_null_notifier_on_invalid_address(self):
        with patch(
            "terok_clearance.notifications.desktop.MessageBus",
            side_effect=ValueError("could not open dbus info file"),
        ):
            notifier = await create_notifier()
            assert isinstance(notifier, NullNotifier)
