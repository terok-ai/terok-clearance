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

from dbus_fast import Message, MessageType
from dbus_fast.aio import MessageBus
from dbus_fast.introspection import Node as _IntrospectionNode

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

    #: Sender-agnostic match rule for the two notification signals we
    #: subscribe to.  Strict ``key='value'`` quoting (per the D-Bus
    #: spec § Match-Rule Syntax) so dbus-broker — which rejects
    #: unquoted values — accepts it as readily as the more lenient
    #: reference daemon.  No ``sender=`` filter is set, by design: on
    #: relay-fronted setups (e.g. xdg-desktop-portal in front of GNOME
    #: Shell) the well-known name ``org.freedesktop.Notifications`` is
    #: owned by the relay's unique name, while ``ActionInvoked`` /
    #: ``NotificationClosed`` arrive from whatever the relay forwards
    #: through.  Filtering on path + interface is enough — we control
    #: which path the proxy is on and the interface name is unique to
    #: the spec.
    _SIGNAL_MATCH_RULE = f"type='signal',interface='{INTERFACE_NAME}',path='{OBJECT_PATH}'"

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
            # Double-checked locking: another task may have set ``_conn``
            # between the first check and acquiring the lock.  Mypy can't
            # see the concurrent write so it treats the second check as
            # unreachable.
            if self._conn is not None:
                return  # type: ignore[unreachable]
            bus = await MessageBus().connect()
            try:
                # Build the proxy from a hand-rolled XML — the
                # spec-defined shape — instead of a runtime introspect
                # so the method-call surface (``call_notify``,
                # ``call_close_notification``) is available without a
                # round-trip and without depending on what the session
                # daemon's Introspect happens to return.
                proxy = bus.get_proxy_object(
                    BUS_NAME,
                    OBJECT_PATH,
                    _IntrospectionNode.parse(_NOTIFICATIONS_INTROSPECTION_XML),
                )
                iface = proxy.get_interface(INTERFACE_NAME)
                # Subscribe via a raw bus message handler instead of
                # the proxy's ``on_<signal>`` setattrs.  dbus_fast's
                # proxy interface filters incoming signals on a
                # ``msg.sender == bus._name_owners[bus_name]`` check
                # — which silently drops every ``ActionInvoked`` on
                # relay-fronted setups (xdg-desktop-portal in front
                # of GNOME Shell, for one) where the well-known
                # ``org.freedesktop.Notifications`` is owned by the
                # relay but signals arrive from whichever process
                # the relay forwards through.  The send path
                # (``call_notify``) is unaffected by that filter,
                # which is exactly the asymmetric symptom that
                # produced this fix: popups appear, button clicks
                # vanish.  A raw handler + a sender-agnostic match
                # rule ([`_SIGNAL_MATCH_RULE`][terok_clearance.notifications.desktop.DbusNotifier._SIGNAL_MATCH_RULE])
                # bypasses ``_name_owners`` entirely.
                bus.add_message_handler(self._dispatch_signal)
                # Diagnostic: confirm the handler actually landed in the
                # bus's handler list.  ``getattr`` defends against future
                # dbus_fast renames of the private cache.
                handler_count = len(getattr(bus, "_user_message_handlers", []))
                _log.info(
                    "registered _dispatch_signal — bus has %d user handler(s)",
                    handler_count,
                )
                await self._add_signal_match(bus)
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

    async def _add_signal_match(self, bus: MessageBus) -> None:
        """Register our signal match rule with the bus daemon.

        Logs at WARNING and continues if the bus refuses the rule —
        the notifier still produces popups in that degraded state,
        which beats an exception that takes the whole daemon down.
        """
        reply = await bus.call(
            Message(
                destination="org.freedesktop.DBus",
                interface="org.freedesktop.DBus",
                path="/org/freedesktop/DBus",
                member="AddMatch",
                signature="s",
                body=[self._SIGNAL_MATCH_RULE],
            )
        )
        if reply is not None and reply.message_type == MessageType.ERROR:
            _log.warning(
                "AddMatch refused for %r: %s — popup actions will not be received",
                self._SIGNAL_MATCH_RULE,
                reply.body,
            )
            return
        _log.info("AddMatch accepted for %r", self._SIGNAL_MATCH_RULE)

    def _dispatch_signal(self, msg: Message) -> None:
        """Filter incoming bus messages and dispatch the two we care about.

        Returns ``None`` so dbus_fast keeps routing the message to
        any other registered handlers.  The filter is in two layers:

        1. **Spec-shape guard.**  Drop anything that isn't a signal on
           our path + interface — that's noise from other handlers
           sharing the bus connection.
        2. **Authenticated-sender guard.**  Compare ``msg.sender``
           against the bus's resolved unique-name owner of
           ``org.freedesktop.Notifications`` (populated automatically
           on every reply we receive from the daemon, including the
           ``Notify`` we always make before the first action arrives,
           and kept current by the bus's ``NameOwnerChanged``
           subscription).  Any mismatch is a local peer trying to
           spoof a verdict — log + drop.

        We do this in code rather than via a ``sender=`` clause on
        the AddMatch rule because match-rule sender filters resolve
        once at AddMatch time on some bus daemons and don't follow
        ownership churn; on relay topologies (xdg-desktop-portal in
        front of GNOME Shell, observed in the wild) the runtime
        cache is the only source of truth that tracks the relay's
        unique name correctly.

        INFO-level logging on every signal we accept gives the
        operator a journald breadcrumb confirming the dispatch path
        is alive — silent failure was diagnostic-hostile and turning
        every popup-action investigation into a dbus-monitor session.
        """
        if (
            msg.message_type != MessageType.SIGNAL
            or msg.interface != INTERFACE_NAME
            or msg.path != OBJECT_PATH
        ):
            return
        # Diagnostic — fires for every notifications-iface signal we
        # see, before any further filtering.  When the dispatch path
        # is broken (e.g. the bus reader stalls or the message
        # handler isn't actually wired up) the absence of this log on
        # a click is the giveaway.
        _log.info(
            "notifications signal received: member=%r sender=%r body=%r",
            msg.member,
            msg.sender,
            msg.body,
        )
        if not self._sender_is_authentic(msg):
            return
        if msg.member == "ActionInvoked":
            try:
                nid, action_key = msg.body
            except ValueError:
                _log.warning("ActionInvoked with unexpected body: %r", msg.body)
                return
            self._handle_action(int(nid), str(action_key))
        elif msg.member == "NotificationClosed":
            try:
                nid, reason = msg.body
            except ValueError:
                _log.warning("NotificationClosed with unexpected body: %r", msg.body)
                return
            self._handle_closed(int(nid), int(reason))

    def _sender_is_authentic(self, msg: Message) -> bool:
        """Reject signals that don't come from the resolved Notifications owner.

        See [`_dispatch_signal`][terok_clearance.notifications.desktop.DbusNotifier._dispatch_signal]
        for the threat model.  Returns ``True`` to authorize dispatch,
        ``False`` to drop with a WARNING-level breadcrumb.
        """
        if self._conn is None:
            return False
        # ``_name_owners`` is populated on every method-call reply
        # destined for a well-known name — guaranteed to hold our key
        # by the time the daemon emits the first ``ActionInvoked``,
        # because we always ``Notify`` first.  ``getattr`` defends
        # against future dbus_fast renames of the private cache.
        owners: Mapping[str, str] = getattr(self._conn.bus, "_name_owners", {})
        expected = owners.get(BUS_NAME, "")
        if not expected:
            _log.warning(
                "rejecting %s: bus has not resolved %r owner yet",
                msg.member,
                BUS_NAME,
            )
            return False
        if msg.sender != expected:
            _log.warning(
                "rejecting %s from %r (expected %r — possible local-bus spoof)",
                msg.member,
                msg.sender,
                expected,
            )
            return False
        return True

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
        try:
            conn.bus.remove_message_handler(self._dispatch_signal)
        except Exception:
            # Best-effort: a torn-down bus already dropped its handler
            # registry, and we're disconnecting anyway.
            _log.debug("remove_message_handler raised during disconnect", exc_info=True)
        conn.bus.disconnect()
        self._conn = None
        self._callbacks.clear()
