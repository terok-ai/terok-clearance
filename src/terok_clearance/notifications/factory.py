# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Session-bus probing factory for the appropriate [`Notifier`][].

Thin convenience: try a real [`DbusNotifier`][], fall back to a
[`NullNotifier`][] if no session bus is reachable.  Lives at the
same layer as the concrete backends so CLI / consumer code can reach
it without importing the package root (which causes a layering
circularity — ``interface → interface``).
"""

import logging

from dbus_fast import DBusError

from terok_clearance.notifications.desktop import DbusNotifier
from terok_clearance.notifications.null import NullNotifier
from terok_clearance.notifications.protocol import Notifier

_log = logging.getLogger(__name__)


async def create_notifier(app_name: str = "terok") -> Notifier:
    """Return a connected ``DbusNotifier``, or a ``NullNotifier`` on failure.

    Args:
        app_name: Application name sent with every notification.

    Returns:
        A ``Notifier``-compatible instance.
    """
    notifier = DbusNotifier(app_name)
    try:
        await notifier.connect()
    except (OSError, DBusError, ValueError) as exc:
        _log.debug("D-Bus session bus unavailable, falling back to NullNotifier: %s", exc)
        return NullNotifier()
    return notifier
