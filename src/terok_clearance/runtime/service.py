# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Shared helpers for the standalone ``serve()`` entry points.

The hub ([`terok_clearance.hub.server.serve`][terok_clearance.hub.server.serve]) and the
verdict helper ([`terok_clearance.verdict.server.serve`][terok_clearance.verdict.server.serve])
both expose ``serve()`` coroutines that drive the standalone CLI
verbs (``terok-clearance-hub serve`` / ``serve-verdict``) used by
integration tests.  Both need the same two pieces of plumbing: log
to stderr so the launching process picks it up, and block on
``SIGINT`` / ``SIGTERM`` until the operator tears them down.

The per-container supervisor in terok-sandbox composes
[`ClearanceHub`][terok_clearance.ClearanceHub] and
[`VerdictServer`][terok_clearance.VerdictServer] directly — it owns
its own lifecycle and signal handling, so it does not go through
these helpers.
"""

from __future__ import annotations

import asyncio
import logging
import signal
import sys


def configure_logging(level: int = logging.INFO) -> None:
    """Send INFO-level logs to stderr so the launching process picks them up."""
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=level,
        stream=sys.stderr,
    )


async def wait_for_shutdown_signal() -> None:  # pragma: no cover — real signals
    """Block the current task until ``SIGINT`` or ``SIGTERM`` arrives."""
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, stop.set)
    await stop.wait()
