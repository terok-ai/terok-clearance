# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Clearance hub + desktop notification library for terok.

Two unrelated wire formats live under this one package:

* ``org.terok.Clearance1`` over a unix-socket **varlink** transport —
  the hub (:class:`ClearanceHub`) and the client library
  (:class:`ClearanceClient`, :class:`EventSubscriber`) that drive the
  per-container block / verdict / lifecycle flow.
* ``org.freedesktop.Notifications`` over **D-Bus** — the
  :class:`DbusNotifier` wrapper that renders those events as desktop
  popups.  Kept because that's the OS API; every other D-Bus path in
  this package (``org.terok.Shield1``) was removed in favour of the
  varlink transport.
"""

import logging

from dbus_fast import DBusError

from terok_dbus._callback import CallbackNotifier, Notification
from terok_dbus._client import ClearanceClient
from terok_dbus._hub import ClearanceHub, default_clearance_socket_path, serve
from terok_dbus._identity import ContainerIdentity
from terok_dbus._notifier import DbusNotifier
from terok_dbus._null import NullNotifier
from terok_dbus._protocol import Notifier
from terok_dbus._subscriber import EventSubscriber
from terok_dbus._wire import (
    CLEARANCE_INTERFACE_NAME,
    Clearance1Interface,
    ClearanceEvent,
    InvalidAction,
    ShieldCliFailed,
    UnknownRequest,
    VerdictTupleMismatch,
)

__all__ = [
    "CLEARANCE_INTERFACE_NAME",
    "CallbackNotifier",
    "Clearance1Interface",
    "ClearanceClient",
    "ClearanceEvent",
    "ClearanceHub",
    "ContainerIdentity",
    "DbusNotifier",
    "EventSubscriber",
    "InvalidAction",
    "Notification",
    "Notifier",
    "NullNotifier",
    "ShieldCliFailed",
    "UnknownRequest",
    "VerdictTupleMismatch",
    "create_notifier",
    "default_clearance_socket_path",
    "serve",
]

__version__ = "0.0.0"

_log = logging.getLogger(__name__)


async def create_notifier(app_name: str = "terok") -> Notifier:
    """Return a connected ``DbusNotifier``, or a ``NullNotifier`` on failure.

    Thin convenience wrapper — calls :meth:`DbusNotifier.connect` and
    falls through to :class:`NullNotifier` if no session bus is
    reachable.  Unrelated to the clearance varlink transport; this one
    is about desktop popups.

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
