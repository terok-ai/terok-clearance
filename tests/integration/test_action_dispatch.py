# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Inbound signal dispatch, over a real bus.

The send path (``Notify``) and the receive path (``ActionInvoked``) fail
independently: a broken receive path leaves popups appearing exactly as
before while every button click vanishes.  Nothing else in the suite
exercises receive against a real daemon — the unit tests drive a fake
bus, so they keep passing even when the dispatch guard rejects
everything.

That guard reads ``MessageBus._name_owners`` and
``_user_message_handlers`` — private dbus_fast attributes.  Both are
``getattr``-guarded, and the fallback for ``_name_owners`` is an empty
mapping, which makes ``_sender_is_authentic`` reject *every* signal.  So
an upstream rename degrades silently, and only a test that emits a real
signal and waits for the callback can tell.
"""

from __future__ import annotations

import asyncio

import pytest
from dbus_fast import Message, MessageType, Variant
from dbus_fast.aio import MessageBus
from dbus_fast.auth import AuthExternal

from terok_clearance.notifications.desktop import (
    BUS_NAME,
    INTERFACE_NAME,
    OBJECT_PATH,
    DbusNotifier,
)
from terok_clearance.notifications.protocol import Notifier

pytestmark = pytest.mark.needs_dbus

#: python-dbusmock's control interface on every mocked object.
MOCK_INTERFACE = "org.freedesktop.DBus.Mock"

ACTION_ID = "allow"
ACTION_LABEL = "Allow"
SPOOFED_ACTION_ID = "spoofed-allow"

#: Generous enough for a signal round-trip on a loaded matrix slot, short
#: enough that the drop case does not stall the suite.
ACTION_DISPATCH_TIMEOUT_S = 5.0


async def _emit_as_daemon(notification_id: int, action_id: str) -> None:
    """Make the mock daemon itself emit ``ActionInvoked``.

    python-dbusmock exposes ``EmitSignal`` on ``org.freedesktop.DBus.Mock``,
    so the signal leaves the process that owns the well-known name -- its
    sender is the unique name the notifier's guard resolved, which is the
    only way to exercise the authentic path.
    """
    bus = await MessageBus(auth=AuthExternal()).connect()
    try:
        reply = await bus.call(
            Message(
                destination=BUS_NAME,
                path=OBJECT_PATH,
                interface=MOCK_INTERFACE,
                member="EmitSignal",
                signature="sssav",
                body=[
                    INTERFACE_NAME,
                    "ActionInvoked",
                    "us",
                    [Variant("u", notification_id), Variant("s", action_id)],
                ],
            )
        )
        assert reply is not None and reply.message_type is not MessageType.ERROR, reply.body
    finally:
        bus.disconnect()


async def _emit_action_invoked(notification_id: int, action_id: str) -> None:
    """Emit ``ActionInvoked`` from a *second* connection to the same bus.

    The mock daemon has no "click" verb, so the signal is emitted
    directly.  This connection is not the daemon, so its sender name
    differs from the resolved owner of ``org.freedesktop.Notifications``
    -- which is exactly what the authenticity guard exists to catch, and
    why the spoof case below expects a drop.
    """
    bus = await MessageBus(auth=AuthExternal()).connect()
    try:
        bus.send(
            Message(
                message_type=MessageType.SIGNAL,
                path=OBJECT_PATH,
                interface=INTERFACE_NAME,
                member="ActionInvoked",
                signature="us",
                body=[notification_id, action_id],
            )
        )
    finally:
        bus.disconnect()


class TestActionDispatch:
    """``ActionInvoked`` reaches the registered callback -- or is dropped."""

    async def test_action_from_the_daemon_reaches_the_callback(
        self, notifier: Notifier, dbusmock_session, notification_daemon
    ) -> None:
        """The whole receive path, end to end on a real bus.

        Falsifies: a dbus_fast rename of the private caches (the guard
        would reject every signal), a lost message handler, a match rule
        the daemon refused, and any regression in the signal decode.
        """
        assert isinstance(notifier, DbusNotifier)
        clicked: asyncio.Future[str] = asyncio.get_running_loop().create_future()

        notification_id = await notifier.notify(
            "Verdict needed", "Allow this?", actions=((ACTION_ID, ACTION_LABEL),)
        )
        await notifier.on_action(
            notification_id,
            lambda action_id: clicked.done() or clicked.set_result(action_id),
        )

        await _emit_as_daemon(notification_id, ACTION_ID)

        action = await asyncio.wait_for(clicked, timeout=ACTION_DISPATCH_TIMEOUT_S)
        assert action == ACTION_ID

    async def test_action_from_a_stranger_is_dropped(
        self, notifier: Notifier, dbusmock_session, notification_daemon
    ) -> None:
        """A peer that does not own the well-known name cannot forge a verdict."""
        assert isinstance(notifier, DbusNotifier)
        clicked: asyncio.Future[str] = asyncio.get_running_loop().create_future()

        notification_id = await notifier.notify(
            "Verdict needed", "Allow this?", actions=((ACTION_ID, ACTION_LABEL),)
        )
        await notifier.on_action(
            notification_id,
            lambda action_id: clicked.done() or clicked.set_result(action_id),
        )

        await _emit_action_invoked(notification_id, SPOOFED_ACTION_ID)

        with pytest.raises(asyncio.TimeoutError):
            await asyncio.wait_for(clicked, timeout=ACTION_DISPATCH_TIMEOUT_S)
