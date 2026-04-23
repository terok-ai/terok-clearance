# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""D-Bus addressing constants and notification close-reason codes."""

from enum import IntEnum

BUS_NAME = "org.freedesktop.Notifications"
"""Well-known bus name for the freedesktop Notifications service."""

OBJECT_PATH = "/org/freedesktop/Notifications"
"""Object path for the Notifications interface."""

INTERFACE_NAME = "org.freedesktop.Notifications"
"""D-Bus interface name for the Notifications spec."""


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
