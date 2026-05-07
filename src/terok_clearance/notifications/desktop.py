# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Desktop notifier backed by dbus-fast and the freedesktop Notifications spec."""

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import IntEnum
from pathlib import Path
from typing import Any

from dbus_fast.aio import MessageBus

_log = logging.getLogger(__name__)

#: Pango markup characters that gnome-shell parses inside notification
#: summary / body strings.  Inputs reach this point already sanitised
#: to printable ASCII by the hub's wire-format boundary, so only the
#: three markup-meaningful element characters need a renderer-local
#: pre-escape — ``"`` and ``'`` are literal inside element content per
#: the Pango spec, newlines (legitimate body-line separators) survive,
#: and every other printable byte passes through unchanged.  Order
#: matters: ``&`` must be replaced first or a later ``&lt;`` would be
#: re-escaped to ``&amp;lt;``.
_PANGO_ESCAPES: tuple[tuple[str, str], ...] = (
    ("&", "&amp;"),
    ("<", "&lt;"),
    (">", "&gt;"),
)


def _pango_escape(text: str) -> str:
    """Escape the three Pango-markup element characters in *text*."""
    for raw, escaped in _PANGO_ESCAPES:
        text = text.replace(raw, escaped)
    return text


#: Addressing for the freedesktop Notifications service.  Exposed at
#: module scope so tests + out-of-tree consumers can reference the
#: same literals the backend dispatches against, but callers in new
#: code should reach for the [`Notifier`][terok_clearance.notifications.protocol.Notifier] abstraction
#: instead of re-implementing the spec.
BUS_NAME = "org.freedesktop.Notifications"
OBJECT_PATH = "/org/freedesktop/Notifications"
INTERFACE_NAME = "org.freedesktop.Notifications"

#: Hand-rolled introspection XML for the freedesktop Notifications
#: interface — the only methods + signals our notifier touches.  Used
#: instead of a runtime ``bus.introspect()`` because the spec is fixed
#: and some daemons (observed on certain GNOME-Shell versions) return
#: introspection data missing the ``ActionInvoked`` /
#: ``NotificationClosed`` signal nodes — which silently strips the
#: ``on_action_invoked`` / ``on_notification_closed`` setattrs from
#: the dbus_fast proxy interface and therefore the operator's clicks
#: into the popup go nowhere while the send path keeps working.  The
#: bug is environment-dependent; baking the spec means we don't
#: depend on the daemon's introspection completeness.  Source: the
#: freedesktop Desktop Notifications spec, sections 1.3 (methods) and
#: 1.4 (signals).
_NOTIFICATIONS_INTROSPECTION_XML = """\
<node>
  <interface name="org.freedesktop.Notifications">
    <method name="Notify">
      <arg type="s" name="app_name" direction="in"/>
      <arg type="u" name="replaces_id" direction="in"/>
      <arg type="s" name="app_icon" direction="in"/>
      <arg type="s" name="summary" direction="in"/>
      <arg type="s" name="body" direction="in"/>
      <arg type="as" name="actions" direction="in"/>
      <arg type="a{sv}" name="hints" direction="in"/>
      <arg type="i" name="expire_timeout" direction="in"/>
      <arg type="u" name="id" direction="out"/>
    </method>
    <method name="CloseNotification">
      <arg type="u" name="id" direction="in"/>
    </method>
    <signal name="NotificationClosed">
      <arg type="u" name="id"/>
      <arg type="u" name="reason"/>
    </signal>
    <signal name="ActionInvoked">
      <arg type="u" name="id"/>
      <arg type="s" name="action_key"/>
    </signal>
  </interface>
</node>
"""


class CloseReason(IntEnum):
    """Reason a notification was closed, per the freedesktop spec."""

    EXPIRED = 1
    """The notification expired (timed out)."""

    DISMISSED = 2
    """The notification was dismissed by the user."""

    CLOSED = 3
    """The notification was closed via ``CloseNotification``."""

    UNDEFINED = 4
    """The notification server did not provide a reason."""


# ``Path(__file__)`` can be relative under editable installs or alternative
# loaders; ``resolve()`` before ``as_uri()`` because the latter rejects
# relative paths with a ValueError that would fire at import time and
# prevent the module from loading at all.
_LOGO_PATH = Path(__file__).resolve().parent.parent / "resources" / "terok-logo.png"

#: ``file://`` URI of the bundled terok logo.  Freedesktop daemons render a
#: PNG passed as ``app_icon`` alongside summary + body; this gives every
#: clearance notification a consistent brand mark without requiring the
#: operator to install a system icon theme.  Empty when the resource is
#: missing (editable installs that skipped package-data copy, tests running
#: against a checked-out source tree without the file) — callers fall
#: through to no icon.
_DEFAULT_APP_ICON = _LOGO_PATH.as_uri() if _LOGO_PATH.is_file() else ""


@dataclass(frozen=True)
class _Connection:
    """A live session-bus handle paired with its Notifications proxy interface."""

    bus: MessageBus
    interface: Any  # dbus_fast ProxyInterface — dynamic-attribute object


class DbusNotifier:
    """Send desktop notifications over the D-Bus session bus.

    The connection is established lazily on the first ``notify`` call.
    Action callbacks are dispatched from the ``ActionInvoked`` signal;
    stale callbacks are cleaned up automatically on ``NotificationClosed``.

    Args:
        app_name: Application name sent with every notification.
    """

    def __init__(self, app_name: str = "terok") -> None:
        """Initialise with the given application name."""
        self._app_name = app_name
        self._conn: _Connection | None = None
        self._callbacks: dict[int, Callable[[str], None]] = {}
        self._connect_lock = asyncio.Lock()

    async def connect(self) -> None:
        """Idempotently open the session-bus connection and subscribe to signals.

        Safe to call concurrently and repeatedly: the lock serialises racing
        callers so exactly one MessageBus is ever created for this notifier.
        """
        if self._conn is not None:
            return
        async with self._connect_lock:
            if self._conn is not None:
                return
            bus = await MessageBus().connect()
            try:
                # Build the proxy from a hand-rolled XML — the
                # spec-defined shape — instead of a runtime introspect
                # so signal registration works regardless of what the
                # session daemon's Introspect happens to return.  See
                # _NOTIFICATIONS_INTROSPECTION_XML for the rationale.
                proxy = bus.get_proxy_object(
                    BUS_NAME, OBJECT_PATH, _NOTIFICATIONS_INTROSPECTION_XML
                )
                iface = proxy.get_interface(INTERFACE_NAME)
                # The spec guarantees both signals; the previous
                # ``hasattr`` guard covered for missing introspection
                # but would silently mask a regression.  Subscribe
                # unconditionally; an attribute error here is a real
                # bug we want to see surface.
                iface.on_action_invoked(self._handle_action)
                iface.on_notification_closed(self._handle_closed)
            except BaseException:
                # Catch ``BaseException`` so an ``asyncio.CancelledError``
                # (``BaseException`` subclass on 3.11+) mid-handshake doesn't
                # leak the already-connected bus.
                bus.disconnect()
                raise
            self._conn = _Connection(bus=bus, interface=iface)
            _log.info(
                "DbusNotifier connected as %r — ActionInvoked / NotificationClosed subscribed",
                self._app_name,
            )

    def _handle_action(self, notification_id: int, action_key: str) -> None:
        """Dispatch an ``ActionInvoked`` signal to the registered callback."""
        callback = self._callbacks.get(notification_id)
        # Log at INFO so operators can confirm in journald that the
        # daemon's ActionInvoked signal reached the notifier (and tell
        # us which case fired): the silent failure mode this method
        # used to permit was diagnostic-hostile because a missed click
        # and a never-emitted signal looked identical.
        if callback is None:
            _log.info(
                "ActionInvoked id=%d action=%r — no registered callback (popup expired?)",
                notification_id,
                action_key,
            )
            return
        _log.info("ActionInvoked id=%d action=%r — dispatching", notification_id, action_key)
        callback(action_key)

    def _handle_closed(self, notification_id: int, _reason: int) -> None:
        """Remove the callback for a closed notification."""
        self._callbacks.pop(notification_id, None)

    async def notify(
        self,
        summary: str,
        body: str = "",
        *,
        actions: Sequence[tuple[str, str]] = (),
        timeout_ms: int = -1,
        hints: Mapping[str, Any] | None = None,
        replaces_id: int = 0,
        app_icon: str = "",
        container_id: str = "",  # noqa: ARG002 — protocol kwarg ignored by desktop
        container_name: str = "",  # noqa: ARG002 — protocol kwarg ignored by desktop
        project: str = "",  # noqa: ARG002 — protocol kwarg ignored by desktop
        task_id: str = "",  # noqa: ARG002 — protocol kwarg ignored by desktop
        task_name: str = "",  # noqa: ARG002 — protocol kwarg ignored by desktop
    ) -> int:
        """Send a desktop notification.

        Freedesktop notifications render summary + body + actions only,
        so the structured identity kwargs (``container_id`` and the
        terok task triple) are dropped on the floor here — callers are
        expected to have folded the user-facing identity into ``body``
        already.  The kwargs stay in the signature for
        [`Notifier`][terok_clearance.notifications.protocol.Notifier] conformance so callers
        don't have to branch on notifier kind.
        """
        await self.connect()
        assert self._conn is not None  # connect() post-condition

        actions_flat: list[str] = []
        for action_id, label in actions:
            actions_flat.extend((action_id, label))

        return await self._conn.interface.call_notify(
            self._app_name,
            replaces_id,
            app_icon or _DEFAULT_APP_ICON,
            _pango_escape(summary),
            _pango_escape(body),
            actions_flat,
            dict(hints) if hints is not None else {},
            timeout_ms,
        )

    async def on_action(
        self,
        notification_id: int,
        callback: Callable[[str], None],
    ) -> None:
        """Register a callback for when the user clicks an action button.

        Args:
            notification_id: ID returned by ``notify``.
            callback: Called with the ``action_id`` string when invoked.
        """
        self._callbacks[notification_id] = callback

    async def close(self, notification_id: int) -> None:
        """Close an active notification.

        Args:
            notification_id: ID returned by ``notify``.
        """
        self._callbacks.pop(notification_id, None)
        if self._conn is not None:
            await self._conn.interface.call_close_notification(notification_id)

    async def disconnect(self) -> None:
        """Tear down the session-bus connection."""
        conn = self._conn
        if conn is None:
            return
        if hasattr(conn.interface, "off_action_invoked"):
            conn.interface.off_action_invoked(self._handle_action)
        if hasattr(conn.interface, "off_notification_closed"):
            conn.interface.off_notification_closed(self._handle_closed)
        conn.bus.disconnect()
        self._conn = None
        self._callbacks.clear()
