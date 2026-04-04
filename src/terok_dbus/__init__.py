# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""D-Bus desktop notification package for terok."""

import logging

from dbus_fast import DBusError

from terok_dbus._notifier import DbusNotifier
from terok_dbus._null import NullNotifier
from terok_dbus._protocol import Notifier

__all__ = [
    "DbusNotifier",
    "NullNotifier",
    "Notifier",
    "create_notifier",
]

__version__ = "0.0.0"

_log = logging.getLogger(__name__)


async def create_notifier(app_name: str = "terok") -> Notifier:
    """Return a connected ``DbusNotifier``, or a ``NullNotifier`` on failure.

    This is the primary entry point. Callers get a working notifier without
    caring whether a D-Bus session bus is available.

    Args:
        app_name: Application name sent with every notification.

    Returns:
        A ``Notifier``-compatible instance.
    """
    notifier = DbusNotifier(app_name)
    try:
        await notifier._connect()
    except (OSError, DBusError) as exc:
        _log.debug("D-Bus session bus unavailable, falling back to NullNotifier: %s", exc)
        return NullNotifier()
    return notifier
