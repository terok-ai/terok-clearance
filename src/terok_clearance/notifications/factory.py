# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Session-bus probing factory + the no-op fallback notifier.

Thin convenience: try a real [`DbusNotifier`][terok_clearance.notifications.desktop.DbusNotifier],
fall back to a [`NullNotifier`][terok_clearance.notifications.factory.NullNotifier] if no
session bus is reachable.  Lives at the same layer as the concrete
backends so CLI / consumer code can reach it without importing the
package root (which causes a layering circularity — ``interface → interface``).

The no-op ``NullNotifier`` is co-located here because it has no
state of its own and the factory is its only constructor in
production code — keeping both in one module reduces the notifier
surface to ``factory + desktop + protocol + callback`` instead of
five files.
"""

import logging
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from dbus_fast import DBusError

from terok_clearance.notifications.desktop import DbusNotifier
from terok_clearance.notifications.protocol import Notifier

_log = logging.getLogger(__name__)


class NullNotifier:
    """Silent fallback that satisfies the ``Notifier`` protocol.

    Every method is a no-op. ``notify`` always returns ``0``.
    """

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
        container_id: str = "",
        container_name: str = "",
        project: str = "",
        task_id: str = "",
        task_name: str = "",
    ) -> int:
        """Accept and discard a notification, returning ``0``."""
        return 0

    async def on_action(
        self,
        notification_id: int,
        callback: Callable[[str], None],
    ) -> None:
        """Accept and discard an action callback registration."""

    async def close(self, notification_id: int) -> None:
        """Accept and discard a close request."""

    async def disconnect(self) -> None:
        """Accept and discard a teardown request."""


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
