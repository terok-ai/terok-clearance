# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Clearance hub + desktop notification library for terok.

The operator-UI plane for terok-shield: turns shield's blocked-
connection events into Allow/Deny prompts and routes the operator's
verdict back to shield for enforcement.  Two axes of pluggability
apply:

* **Producer (event source) — closed.**  Shield is the only
  producer.  The wire vocabulary (``shield_up``, ``connection_blocked``,
  …) names shield's state machine, and the verdict path execs
  ``terok-shield allow|deny``.  A non-shield "clearance" wouldn't
  work end-to-end; the package is shield's UI plane, not a generic
  firewall console.
* **Operator UI (consumer) — open.**  Anything that subscribes to
  the hub's varlink stream and implements the
  [`Notifier`][terok_clearance.notifications.protocol.Notifier] protocol on
  the verdict-routing side is a valid UI: today the D-Bus desktop notifier
  ([`DbusNotifier`][terok_clearance.notifications.desktop.DbusNotifier]),
  the standalone Textual ``terok clearance`` app, and the embedded
  ``terok-tui`` screen all ride on this seam.

Container-runtime inspection is no longer a clearance concern: the
shield reader resolves the orchestrator-supplied dossier at emit
time and ships it on the wire (``ClearanceEvent.dossier``), so
clearance has no Python-level coupling to any runtime.

Two unrelated wire formats live under this one package as a result:

* ``org.terok.Clearance1`` over a unix-socket **varlink** transport —
  the hub ([`ClearanceHub`][terok_clearance.ClearanceHub]) and the client library
  ([`ClearanceClient`][terok_clearance.ClearanceClient], [`EventSubscriber`][terok_clearance.EventSubscriber]) that drive the
  per-container block / verdict / lifecycle flow.
* ``org.freedesktop.Notifications`` over **D-Bus** — the
  [`DbusNotifier`][terok_clearance.notifications.desktop.DbusNotifier]
  wrapper that renders those events as desktop popups.  Kept because
  that's the OS API; the in-package transport is varlink.

The supervisor (in terok-sandbox) composes one
[`ClearanceHub`][terok_clearance.ClearanceHub] and one
[`VerdictServer`][terok_clearance.VerdictServer] in-process per
container.  Each container has its own hub socket, so operator UIs
multiplex across the per-container sockets via
[`MultiSocketSubscriber`][terok_clearance.MultiSocketSubscriber].
"""

from typing import TYPE_CHECKING

# Lazy re-export map: public name → the submodule that defines it.  The
# barrel is Tier-A — importing ``terok_clearance`` binds nothing beyond
# this table, so ``asyncvarlink`` (hub/client/verdict), ``dbus_fast``
# (subscriber/notifications) and even ``terok_util`` (``COMMANDS``) stay
# off the import path until the matching symbol is first touched.
_LAZY = {
    "ALL_NOTIFY_CATEGORIES": "terok_clearance.client.subscriber",
    "NOTIFY_BLOCKED": "terok_clearance.client.subscriber",
    "NOTIFY_VERDICT": "terok_clearance.client.subscriber",
    "EventSubscriber": "terok_clearance.client.subscriber",
    "MultiSocketSubscriber": "terok_clearance.client.subscriber",
    "ClearanceClient": "terok_clearance.client.client",
    "ClearanceEvent": "terok_clearance.domain.events",
    "ClearanceHub": "terok_clearance.hub.server",
    "VerdictClient": "terok_clearance.verdict.client",
    "VerdictServer": "terok_clearance.verdict.server",
    "CallbackNotifier": "terok_clearance.notifications.callback",
    "Notification": "terok_clearance.notifications.callback",
    "create_notifier": "terok_clearance.notifications.factory",
    "default_clearance_socket_path": "terok_clearance.wire.socket",
    "COMMANDS": "terok_clearance.commands",
}

__all__ = [
    "ALL_NOTIFY_CATEGORIES",
    "COMMANDS",
    "CallbackNotifier",
    "ClearanceClient",
    "ClearanceEvent",
    "ClearanceHub",
    "EventSubscriber",
    "MultiSocketSubscriber",
    "NOTIFY_BLOCKED",
    "NOTIFY_VERDICT",
    "Notification",
    "VerdictClient",
    "VerdictServer",
    "create_notifier",
    "default_clearance_socket_path",
]


def __getattr__(name: str) -> object:
    """Import and cache a public symbol on first access (PEP 562)."""
    try:
        module = _LAZY[name]
    except KeyError:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}") from None
    import importlib

    value = getattr(importlib.import_module(module), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    """List resolved and lazy names alike for tab-completion and ``dir()``."""
    return sorted({*globals(), *_LAZY})


if TYPE_CHECKING:  # keep IDEs and mypy seeing the full public surface
    from terok_clearance.client.client import ClearanceClient
    from terok_clearance.client.subscriber import (
        ALL_NOTIFY_CATEGORIES,
        NOTIFY_BLOCKED,
        NOTIFY_VERDICT,
        EventSubscriber,
        MultiSocketSubscriber,
    )
    from terok_clearance.commands import COMMANDS
    from terok_clearance.domain.events import ClearanceEvent
    from terok_clearance.hub.server import ClearanceHub
    from terok_clearance.notifications.callback import CallbackNotifier, Notification
    from terok_clearance.notifications.factory import create_notifier
    from terok_clearance.verdict.client import VerdictClient
    from terok_clearance.verdict.server import VerdictServer
    from terok_clearance.wire.socket import default_clearance_socket_path

__version__ = "0.0.0"
