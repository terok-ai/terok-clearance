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
"""

from terok_util import ArgDef, CommandDef

from terok_clearance.client.client import ClearanceClient
from terok_clearance.client.subscriber import (
    ALL_NOTIFY_CATEGORIES,
    NOTIFY_BLOCKED,
    NOTIFY_CONTAINER_EXITED,
    NOTIFY_CONTAINER_STARTED,
    NOTIFY_SHIELD_DOWN,
    NOTIFY_SHIELD_UP,
    NOTIFY_VERDICT,
    EventSubscriber,
)
from terok_clearance.commands import COMMANDS
from terok_clearance.domain.events import ClearanceEvent, VerdictAction
from terok_clearance.hub.server import ClearanceHub, serve
from terok_clearance.notifications.callback import CallbackNotifier, Notification
from terok_clearance.notifications.factory import create_notifier
from terok_clearance.runtime.installer import (
    HUB_UNIT_NAME,
    NOTIFIER_UNIT_NAME,
    HubService,
    NotifierService,
    outdated_summary,
)
from terok_clearance.runtime.service import configure_logging, wait_for_shutdown_signal
from terok_clearance.wire.errors import InvalidAction, ShieldCliFailed, UnknownRequest
from terok_clearance.wire.interface import CLEARANCE_INTERFACE_NAME
from terok_clearance.wire.socket import default_clearance_socket_path

__all__ = [
    "ALL_NOTIFY_CATEGORIES",
    "ArgDef",
    "CLEARANCE_INTERFACE_NAME",
    "COMMANDS",
    "CallbackNotifier",
    "ClearanceClient",
    "ClearanceEvent",
    "ClearanceHub",
    "CommandDef",
    "EventSubscriber",
    "HUB_UNIT_NAME",
    "HubService",
    "InvalidAction",
    "NOTIFIER_UNIT_NAME",
    "NOTIFY_BLOCKED",
    "NOTIFY_CONTAINER_EXITED",
    "NOTIFY_CONTAINER_STARTED",
    "NOTIFY_SHIELD_DOWN",
    "NOTIFY_SHIELD_UP",
    "NOTIFY_VERDICT",
    "Notification",
    "NotifierService",
    "ShieldCliFailed",
    "UnknownRequest",
    "VerdictAction",
    "configure_logging",
    "create_notifier",
    "default_clearance_socket_path",
    "outdated_summary",
    "serve",
    "wait_for_shutdown_signal",
]

__version__ = "0.0.0"
