# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""The ``notify`` verb — send a one-shot desktop notification.

Loaded only when ``terok-clearance-hub notify`` is the invoked verb: the
[`COMMAND`][terok_clearance.cli.verbs.notify.COMMAND] node is reached via
the lazy ``source`` reference on a
[`CommandDef`][terok_util.cli_types.CommandDef] in
[`terok_clearance.commands`][terok_clearance.commands], so no other verb
pays for this module's ``dbus_fast`` handler path.
"""

from terok_util import ArgDef, CommandDef


async def _handle_notify(*, summary: str, body: str = "", timeout: int = -1) -> None:
    """Send a one-shot desktop notification and print its ID."""
    from terok_clearance.notifications.factory import create_notifier

    notifier = await create_notifier()
    try:
        notification_id = await notifier.notify(summary, body, timeout_ms=timeout)
        print(notification_id)  # noqa: T201
    finally:
        await notifier.disconnect()


#: The fully-populated ``notify`` verb, resolved lazily from ``commands``.
COMMAND = CommandDef(
    name="notify",
    help="Send a one-shot desktop notification",
    handler=_handle_notify,
    args=(
        ArgDef(name="summary", help="Notification title"),
        ArgDef(name="body", nargs="?", default="", help="Notification body text"),
        ArgDef(
            name="-t/--timeout",
            dest="timeout",
            type=int,
            default=-1,
            help="Expiration timeout in milliseconds (-1 = server default)",
        ),
    ),
)
