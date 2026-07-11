# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""The ``serve-verdict`` verb — run the verdict-helper service.

Loaded only when ``terok-clearance-hub serve-verdict`` is the invoked
verb.  A standalone entry point for integration tests; production
deploys compose the hub + verdict pair in-process via the per-container
supervisor in terok-sandbox.
"""

from terok_util import CommandDef


async def _handle_serve_verdict() -> None:
    """Run the verdict helper service until SIGINT/SIGTERM."""
    from terok_clearance.verdict.server import serve

    await serve()


#: The fully-populated ``serve-verdict`` verb, resolved lazily from ``commands``.
COMMAND = CommandDef(
    name="serve-verdict",
    help="Run the verdict helper (serves org.terok.ClearanceVerdict1 for shield exec)",
    handler=_handle_serve_verdict,
)
