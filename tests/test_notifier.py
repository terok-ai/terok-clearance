# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for DbusNotifier — mocked dbus-fast interactions."""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from terok_clearance.notifications.desktop import DbusNotifier


@pytest.fixture(autouse=True)
def _reset_app_icon_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Reset the resolved-icon cache before every test.

    `_default_app_icon` memoises the first successful icon-theme lookup
    in `_RESOLVED_ICON_NAME` — otherwise the first test that plants a
    fake `terok-symbolic.svg` under tmp_path locks in ``"terok-symbolic"``
    for every subsequent test, breaking the file-URI fallback assertions.
    """
    monkeypatch.setattr(
        "terok_clearance.notifications.desktop._RESOLVED_ICON_NAME",
        None,
        raising=False,
    )


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
    # ``_dispatch_signal`` validates ``msg.sender`` against the bus's
    # ``_name_owners`` cache for ``org.freedesktop.Notifications`` —
    # populate the cache with the relay sender every test signal
    # uses so the legitimate-sender path is exercised by default.
    bus._name_owners = {"org.freedesktop.Notifications": ":1.30"}

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
        from terok_clearance.notifications.desktop import _default_app_icon

        with patch("terok_clearance.notifications.desktop.MessageBus", return_value=mock_bus):
            notifier = DbusNotifier("myapp")
            nid = await notifier.notify("Title", "Body", timeout_ms=5000)
            assert nid == 7
            iface = mock_bus.get_proxy_object.return_value.get_interface.return_value
            # app_icon falls back to the resolved default — the icon-theme
            # name when ``terok setup`` has installed terok-symbolic, else
            # the bundled SVG as a file:// URI.  Either way the same string
            # the production code computes.
            iface.call_notify.assert_awaited_once_with(
                "myapp",
                0,
                _default_app_icon(),
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

    async def test_default_icon_falls_back_to_bundled_svg(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Without ``terok setup`` in the icon theme, default = file:// URI to bundled SVG."""
        from terok_clearance.notifications.desktop import _LOGO_PATH, _default_app_icon

        # Empty XDG search path → icon-theme probe finds nothing → fallback fires.
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        monkeypatch.setenv("XDG_DATA_DIRS", str(tmp_path))

        assert _LOGO_PATH.is_file(), f"packaged logo missing at {_LOGO_PATH}"
        icon = _default_app_icon()
        assert icon.startswith("file://")
        assert icon.endswith("terok-logo.svg")
        assert Path(icon.removeprefix("file://")).is_file()

    async def test_default_icon_prefers_theme_when_installed(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """When terok-symbolic is on the XDG icon path, return the bare icon name.

        The notification daemon resolves the name via the icon theme, which
        triggers symbolic-icon tinting in GTK/Qt — so the icon follows the
        active panel theme without any bundled-file involvement.
        """
        from terok_clearance.notifications.desktop import _default_app_icon

        # Plant a fake terok-symbolic.svg at the spec-mandated location.
        theme_path = tmp_path / "icons" / "hicolor" / "symbolic" / "apps"
        theme_path.mkdir(parents=True)
        (theme_path / "terok-symbolic.svg").write_text("<svg/>")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        monkeypatch.setenv("XDG_DATA_DIRS", "")

        assert _default_app_icon() == "terok-symbolic"

    async def test_default_icon_caches_happy_path(
        self,
        monkeypatch: pytest.MonkeyPatch,
        tmp_path: Path,
    ) -> None:
        """Once the icon-theme entry is found, subsequent calls return it without re-walking XDG.

        XDG dirs can sit on slow / network mounts; the success case is
        memoised so steady-state notifications cost an attribute load.
        Miss case stays uncached so post-startup `terok setup` is picked
        up on the next call without a daemon restart.
        """
        from terok_clearance.notifications import desktop

        theme_path = tmp_path / "icons" / "hicolor" / "symbolic" / "apps"
        theme_path.mkdir(parents=True)
        icon_file = theme_path / "terok-symbolic.svg"
        icon_file.write_text("<svg/>")
        monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path))
        monkeypatch.setenv("XDG_DATA_DIRS", "")

        assert desktop._default_app_icon() == "terok-symbolic"

        # Pull the icon back out from under the resolver: a fresh probe
        # would now return the file-URI fallback, but the cached value
        # holds steady.
        icon_file.unlink()
        assert desktop._default_app_icon() == "terok-symbolic"


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

    async def test_signal_from_unauthenticated_sender_rejected(self, mock_bus: MagicMock) -> None:
        """A spoofed signal from a peer that doesn't own the well-known name is dropped."""
        with patch("terok_clearance.notifications.desktop.MessageBus", return_value=mock_bus):
            notifier = DbusNotifier()
            await notifier.connect()
            cb = MagicMock()
            await notifier.on_action(21, cb)
            # Mock cache says the legitimate owner is ``:1.30`` (default
            # in the fixture); the signal arrives from a different peer.
            notifier._dispatch_signal(_signal_msg("ActionInvoked", [21, "allow"], sender=":1.999"))
            cb.assert_not_called()

    async def test_signal_rejected_when_owner_not_yet_resolved(self, mock_bus: MagicMock) -> None:
        """If the bus hasn't populated the owner cache yet, drop the signal."""
        mock_bus._name_owners = {}
        with patch("terok_clearance.notifications.desktop.MessageBus", return_value=mock_bus):
            notifier = DbusNotifier()
            await notifier.connect()
            cb = MagicMock()
            await notifier.on_action(21, cb)
            notifier._dispatch_signal(_signal_msg("ActionInvoked", [21, "allow"]))
            cb.assert_not_called()
