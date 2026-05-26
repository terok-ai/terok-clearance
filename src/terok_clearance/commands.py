# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Command registry for terok-clearance.

Re-exports [`CommandDef`][terok_util.cli_types.CommandDef] and
[`ArgDef`][terok_util.cli_types.ArgDef] from
[`terok_util`][terok_util] — the shared CLI vocabulary every terok-`*`
sibling package now lines up on — and defines the ``COMMANDS`` tuple as
the single source of truth consumed by both the standalone CLI and the
terok integration layer.

Handler functions are async coroutines accepting ``**kwargs`` that match
the declared [`ArgDef`][terok_util.cli_types.ArgDef] names.
"""

from terok_util import ArgDef, CommandDef

# ── Handler functions ─────────────────────────────────────


async def _handle_notify(*, summary: str, body: str = "", timeout: int = -1) -> None:
    """Send a one-shot desktop notification and print its ID."""
    from terok_clearance.notifications.factory import create_notifier

    notifier = await create_notifier()
    try:
        notification_id = await notifier.notify(summary, body, timeout_ms=timeout)
        print(notification_id)  # noqa: T201
    finally:
        await notifier.disconnect()


async def _handle_serve() -> None:
    """Run the clearance hub service until SIGINT/SIGTERM."""
    from terok_clearance.hub.server import serve

    await serve()


async def _handle_serve_verdict() -> None:
    """Run the verdict-helper service until SIGINT/SIGTERM.

    Standalone entry point for integration tests; production deploys
    compose the hub + verdict pair in-process via the per-container
    supervisor in terok-sandbox.
    """
    from terok_clearance.verdict.server import serve

    await serve()


# ── Clearance handler ────────────────────────────────────


async def _handle_clearance() -> None:
    """Run the interactive terminal clearance tool."""
    from terok_clearance.cli.terminal_clearance import run_clearance

    await run_clearance()


# ── Command definitions ───────────────────────────────────

COMMANDS: tuple[CommandDef, ...] = (
    CommandDef(
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
    ),
    CommandDef(
        name="serve",
        help="Run the clearance hub (serves org.terok.Clearance1 varlink on a unix socket)",
        handler=_handle_serve,
    ),
    CommandDef(
        name="serve-verdict",
        help="Run the verdict helper (serves org.terok.ClearanceVerdict1 for shield exec)",
        handler=_handle_serve_verdict,
    ),
    CommandDef(
        name="clearance",
        help="Interactive terminal tool for shield clearance verdicts",
        handler=_handle_clearance,
    ),
)
