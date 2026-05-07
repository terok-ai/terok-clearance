# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for DbusNotifier — mocked dbus-fast interactions."""

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from terok_clearance.notifications.desktop import DbusNotifier


def _mock_bus() -> MagicMock:
    """Create a mock MessageBus with proxy + signal-pipeline wiring."""
    iface = MagicMock()
    iface.call_notify = AsyncMock(return_value=7)
    iface.call_close_notification = AsyncMock()

    proxy = MagicMock()
    proxy.get_interface.return_value = iface

    bus = MagicMock()
    bus.connect = AsyncMock(return_value=bus)
    bus.introspect = AsyncMock(return_value=MagicMock())
    bus.get_proxy_object.return_value = proxy
    bus.add_message_handler = MagicMock()
    bus.remove_message_handler = MagicMock()
    # ``call`` is awaited inside ``_add_signal_match`` for AddMatch /
    # RemoveMatch — return a fake ``METHOD_RETURN`` reply so the
    # ``message_type == ERROR`` warning branch stays untriggered.
    method_return = MagicMock()
    method_return.message_type = MagicMock()
    method_return.message_type.__eq__ = lambda _self, _other: False  # not ERROR
    bus.call = AsyncMock(return_value=method_return)
    bus.disconnect = MagicMock()

    return bus


@pytest.fixture
def mock_bus() -> MagicMock:
    """A pre-wired mock MessageBus."""
    return _mock_bus()


class TestDbusNotifierConnect:
    """Connection lifecycle tests."""

    async def test_lazy_connect_on_first_notify(self, mock_bus: MagicMock):
        with patch("terok_clearance.notifications.desktop.MessageBus", return_value=mock_bus):
            notifier = DbusNotifier("test-app")
            assert notifier._conn is None
            await notifier.notify("hello")
            assert notifier._conn is not None
            assert notifier._conn.bus is mock_bus

    async def test_connect_subscribes_to_signals(self, mock_bus: MagicMock):
        """Signal pipeline wires a raw bus handler + AddMatch, not proxy on_*."""
        with patch("terok_clearance.notifications.desktop.MessageBus", return_value=mock_bus):
            notifier = DbusNotifier()
            await notifier.connect()
            iface = mock_bus.get_proxy_object.return_value.get_interface.return_value
            # Raw bus message handler — bypasses the proxy interface
            # ``_message_handler`` filter that drops signals from
            # relay-fronted senders.
            mock_bus.add_message_handler.assert_called_once_with(notifier._dispatch_signal)
            # AddMatch is dispatched via a ``bus.call(...)``; we don't
            # assert its inner Message because that's a dbus-fast
            # implementation detail, but we do pin that AddMatch was
            # actually issued (without it the bus would only deliver
            # signals targeted at our unique name, not broadcasts).
            mock_bus.call.assert_awaited_once()
            # Proxy ``on_*`` setattrs are no longer used.
            iface.on_action_invoked.assert_not_called()
            iface.on_notification_closed.assert_not_called()

    async def test_disconnect_clears_state(self, mock_bus: MagicMock):
        with patch("terok_clearance.notifications.desktop.MessageBus", return_value=mock_bus):
            notifier = DbusNotifier()
            await notifier.connect()
            await notifier.disconnect()
            assert notifier._conn is None
            assert notifier._callbacks == {}

    async def test_disconnect_unsubscribes_signals(self, mock_bus: MagicMock):
        """Disconnect removes the raw bus handler before tearing down."""
        with patch("terok_clearance.notifications.desktop.MessageBus", return_value=mock_bus):
            notifier = DbusNotifier()
            await notifier.connect()
            await notifier.disconnect()
            mock_bus.remove_message_handler.assert_called_once_with(notifier._dispatch_signal)

    async def test_connect_failure_disconnects_bus(self, mock_bus: MagicMock):
        mock_bus.get_proxy_object = MagicMock(side_effect=RuntimeError("boom"))
        with patch("terok_clearance.notifications.desktop.MessageBus", return_value=mock_bus):
            notifier = DbusNotifier()
            with pytest.raises(RuntimeError, match="boom"):
                await notifier.connect()
            mock_bus.disconnect.assert_called_once()
            assert notifier._conn is None

    async def test_connect_does_not_call_bus_introspect(self, mock_bus: MagicMock):
        """Spec-defined XML is hand-rolled — no runtime introspect call.

        Some daemons return Introspect XML missing ActionInvoked /
        NotificationClosed; relying on it silently dropped popup-action
        signal subscription.  This guards the regression.
        """
        with patch("terok_clearance.notifications.desktop.MessageBus", return_value=mock_bus):
            notifier = DbusNotifier()
            await notifier.connect()
            mock_bus.introspect.assert_not_called()

    async def test_connect_and_notify_share_the_lock(self, mock_bus: MagicMock):
        """A ``connect()`` + ``notify()`` race must produce exactly one MessageBus."""
        with patch(
            "terok_clearance.notifications.desktop.MessageBus", return_value=mock_bus
        ) as cls:
            notifier = DbusNotifier()
            await asyncio.gather(notifier.connect(), notifier.notify("hi"))
        assert cls.call_count == 1
        mock_bus.connect.assert_awaited_once()


class TestDbusNotifierNotify:
    """Notification sending tests."""

    async def test_notify_passes_correct_args(self, mock_bus: MagicMock):
        from terok_clearance.notifications.desktop import _DEFAULT_APP_ICON

        with patch("terok_clearance.notifications.desktop.MessageBus", return_value=mock_bus):
            notifier = DbusNotifier("myapp")
            nid = await notifier.notify("Title", "Body", timeout_ms=5000)
            assert nid == 7
            iface = mock_bus.get_proxy_object.return_value.get_interface.return_value
            # app_icon falls back to the packaged terok-logo.png when the
            # caller doesn't pass an explicit icon — branding for every
            # clearance notification without operator-side icon setup.
            iface.call_notify.assert_awaited_once_with(
                "myapp",
                0,
                _DEFAULT_APP_ICON,
                "Title",
                "Body",
                [],
                {},
                5000,
            )

    async def test_notify_passes_hints_replaces_id_app_icon(self, mock_bus: MagicMock):
        with patch("terok_clearance.notifications.desktop.MessageBus", return_value=mock_bus):
            notifier = DbusNotifier("myapp")
            hints = {"urgency": "mock_variant", "resident": "mock_bool"}
            await notifier.notify(
                "Title",
                replaces_id=42,
                app_icon="dialog-warning",
                hints=hints,
            )
            iface = mock_bus.get_proxy_object.return_value.get_interface.return_value
            call_args = iface.call_notify.call_args[0]
            assert call_args[1] == 42  # replaces_id
            assert call_args[2] == "dialog-warning"  # app_icon
            assert call_args[6] == {"urgency": "mock_variant", "resident": "mock_bool"}

    async def test_notify_flattens_actions(self, mock_bus: MagicMock):
        with patch("terok_clearance.notifications.desktop.MessageBus", return_value=mock_bus):
            notifier = DbusNotifier()
            await notifier.notify("t", actions=[("allow", "Allow"), ("deny", "Deny")])
            iface = mock_bus.get_proxy_object.return_value.get_interface.return_value
            call_args = iface.call_notify.call_args
            assert call_args[0][5] == ["allow", "Allow", "deny", "Deny"]

    async def test_second_notify_reuses_connection(self, mock_bus: MagicMock):
        with patch("terok_clearance.notifications.desktop.MessageBus", return_value=mock_bus):
            notifier = DbusNotifier()
            await notifier.notify("a")
            await notifier.notify("b")
            mock_bus.connect.assert_awaited_once()

    async def test_concurrent_notify_connects_once(self, mock_bus: MagicMock):
        with patch("terok_clearance.notifications.desktop.MessageBus", return_value=mock_bus):
            notifier = DbusNotifier()
            await asyncio.gather(notifier.notify("a"), notifier.notify("b"))
            mock_bus.connect.assert_awaited_once()

    async def test_explicit_app_icon_wins_over_default(self, mock_bus: MagicMock):
        """Callers supplying ``app_icon`` keep their choice; the logo is a fallback."""
        with patch("terok_clearance.notifications.desktop.MessageBus", return_value=mock_bus):
            notifier = DbusNotifier()
            await notifier.notify("Title", app_icon="dialog-warning")
            iface = mock_bus.get_proxy_object.return_value.get_interface.return_value
            assert iface.call_notify.call_args[0][2] == "dialog-warning"

    async def test_pango_markup_escaped_in_summary_and_body(self, mock_bus: MagicMock):
        """``& < >`` in caller-supplied strings are escaped just before D-Bus.

        The wire-boundary sanitiser leaves these characters intact (they're
        printable ASCII), so renderer-local escaping is the layered defence
        that keeps gnome-shell from interpreting attacker bytes as Pango
        markup.  Newlines (legitimate body separators) survive.
        """
        with patch("terok_clearance.notifications.desktop.MessageBus", return_value=mock_bus):
            notifier = DbusNotifier()
            await notifier.notify(
                "Blocked: <b>evil</b>",
                "host: a&b\nProtocol: <i>TCP</i>",
            )
            iface = mock_bus.get_proxy_object.return_value.get_interface.return_value
            call_args = iface.call_notify.call_args[0]
            # Order matters: '&' is escaped first so '<' doesn't double-escape.
            assert call_args[3] == "Blocked: &lt;b&gt;evil&lt;/b&gt;"
            assert call_args[4] == "host: a&amp;b\nProtocol: &lt;i&gt;TCP&lt;/i&gt;"

    async def test_default_icon_is_shipped_logo_file(self) -> None:
        """The fallback icon resolves to a real on-disk ``terok-logo.png``."""
        from pathlib import Path

        from terok_clearance.notifications.desktop import _DEFAULT_APP_ICON, _LOGO_PATH

        assert _LOGO_PATH.is_file(), f"packaged logo missing at {_LOGO_PATH}"
        assert _DEFAULT_APP_ICON.startswith("file://")
        assert Path(_DEFAULT_APP_ICON.removeprefix("file://")).is_file()


class TestDbusNotifierActions:
    """Action callback dispatch tests."""

    async def test_on_action_registers_callback(self, mock_bus: MagicMock):
        with patch("terok_clearance.notifications.desktop.MessageBus", return_value=mock_bus):
            notifier = DbusNotifier()
            cb = MagicMock()
            await notifier.on_action(7, cb)
            assert 7 in notifier._callbacks

    async def test_handle_action_dispatches(self, mock_bus: MagicMock):
        with patch("terok_clearance.notifications.desktop.MessageBus", return_value=mock_bus):
            notifier = DbusNotifier()
            cb = MagicMock()
            await notifier.on_action(7, cb)
            notifier._handle_action(7, "allow")
            cb.assert_called_once_with("allow")

    async def test_handle_action_ignores_unknown_id(self, mock_bus: MagicMock):
        with patch("terok_clearance.notifications.desktop.MessageBus", return_value=mock_bus):
            notifier = DbusNotifier()
            notifier._handle_action(999, "allow")  # should not raise

    async def test_handle_closed_removes_callback(self, mock_bus: MagicMock):
        with patch("terok_clearance.notifications.desktop.MessageBus", return_value=mock_bus):
            notifier = DbusNotifier()
            await notifier.on_action(7, MagicMock())
            notifier._handle_closed(7, 1)
            assert 7 not in notifier._callbacks

    async def test_close_removes_callback_and_calls_dbus(self, mock_bus: MagicMock):
        with patch("terok_clearance.notifications.desktop.MessageBus", return_value=mock_bus):
            notifier = DbusNotifier()
            await notifier.connect()
            await notifier.on_action(7, MagicMock())
            await notifier.close(7)
            assert 7 not in notifier._callbacks
            iface = mock_bus.get_proxy_object.return_value.get_interface.return_value
            iface.call_close_notification.assert_awaited_once_with(7)


def _signal_msg(member: str, body: list, *, sender: str = ":1.30") -> MagicMock:
    """Build a fake dbus_fast Message resembling an ActionInvoked signal."""
    from dbus_fast import MessageType

    msg = MagicMock()
    msg.message_type = MessageType.SIGNAL
    msg.interface = "org.freedesktop.Notifications"
    msg.path = "/org/freedesktop/Notifications"
    msg.member = member
    msg.body = body
    msg.sender = sender
    return msg


class TestDispatchSignal:
    """Raw bus dispatcher: regression coverage for the relay-fronted bug.

    The original bug had ``ActionInvoked`` signals arriving from a
    relay's unique name (``:1.30``) — neither equal to the well-known
    ``org.freedesktop.Notifications`` nor present in the bus's
    ``_name_owners`` cache for that name — and dbus_fast's proxy
    silently dropped every one.  These tests pin that the new
    dispatcher is sender-agnostic and that filtering is purely on
    interface + path + member.
    """

    async def test_action_invoked_dispatches_regardless_of_sender(
        self, mock_bus: MagicMock
    ) -> None:
        """The exact symptom reproduced in dbus-monitor traces."""
        with patch("terok_clearance.notifications.desktop.MessageBus", return_value=mock_bus):
            notifier = DbusNotifier()
            await notifier.connect()
            cb = MagicMock()
            await notifier.on_action(21, cb)
            # Sender is the relay's unique name — exactly the case
            # that used to be filtered out.
            notifier._dispatch_signal(_signal_msg("ActionInvoked", [21, "allow"], sender=":1.30"))
            cb.assert_called_once_with("allow")

    async def test_notification_closed_dispatches(self, mock_bus: MagicMock) -> None:
        with patch("terok_clearance.notifications.desktop.MessageBus", return_value=mock_bus):
            notifier = DbusNotifier()
            await notifier.connect()
            await notifier.on_action(21, MagicMock())
            notifier._dispatch_signal(_signal_msg("NotificationClosed", [21, 1]))
            assert 21 not in notifier._callbacks

    async def test_other_interface_signals_ignored(self, mock_bus: MagicMock) -> None:
        """A signal on a different interface must not poison our state."""
        with patch("terok_clearance.notifications.desktop.MessageBus", return_value=mock_bus):
            notifier = DbusNotifier()
            await notifier.connect()
            cb = MagicMock()
            await notifier.on_action(21, cb)
            stray = _signal_msg("ActionInvoked", [21, "allow"])
            stray.interface = "org.example.Other"
            notifier._dispatch_signal(stray)
            cb.assert_not_called()

    async def test_malformed_body_does_not_raise(self, mock_bus: MagicMock) -> None:
        """A truncated ActionInvoked body is logged and ignored — never raised."""
        with patch("terok_clearance.notifications.desktop.MessageBus", return_value=mock_bus):
            notifier = DbusNotifier()
            await notifier.connect()
            await notifier.on_action(21, MagicMock())
            notifier._dispatch_signal(_signal_msg("ActionInvoked", [21]))  # too few
