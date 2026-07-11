# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""The ``serve`` verb — run the clearance hub service.

Loaded only when ``terok-clearance-hub serve`` is the invoked verb, so
the ``asyncvarlink`` hub stack in
[`terok_clearance.hub.server`][terok_clearance.hub.server] stays off the
import path of every other verb.
"""

from terok_util import CommandDef


async def _handle_serve() -> None:
    """Run the clearance hub service until SIGINT/SIGTERM."""
    from terok_clearance.hub.server import serve

    await serve()


#: The fully-populated ``serve`` verb, resolved lazily from ``commands``.
COMMAND = CommandDef(
    name="serve",
    help="Run the clearance hub (serves org.terok.Clearance1 varlink on a unix socket)",
    handler=_handle_serve,
)
