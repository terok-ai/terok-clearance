# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""The ``clearance`` verb — run the interactive terminal clearance tool.

Loaded only when ``terok-clearance-hub clearance`` is the invoked verb,
so the Textual app in
[`terok_clearance.cli.terminal_clearance`][terok_clearance.cli.terminal_clearance]
— the heaviest import in the package — never loads for ``notify`` or the
hub/verdict servers.
"""

from terok_util import CommandDef


async def _handle_clearance() -> None:
    """Run the interactive terminal clearance tool."""
    from terok_clearance.cli.terminal_clearance import run_clearance

    await run_clearance()


#: The fully-populated ``clearance`` verb, resolved lazily from ``commands``.
COMMAND = CommandDef(
    name="clearance",
    help="Interactive terminal tool for shield clearance verdicts",
    handler=_handle_clearance,
)
