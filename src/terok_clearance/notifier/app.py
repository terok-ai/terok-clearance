# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Bridge clearance-hub events to desktop popups.

Runs as ``terok-clearance-notifier.service`` — a systemd user unit
paired with the hub's own.  Splitting the roles means headless hosts
(CI, servers) run the hub without pulling in a desktop stack, and
notifier crashes never take the firewall or the hub with them.

Lives in the clearance package (rather than in terok) because the
notifier's job — rendering hub events as desktop popups and routing
operator clicks back as verdicts — has no terok-specific logic.
It's one of several operator UIs that subscribe to the hub on the
**consumer-axis** seam (the others today are the standalone
``terok clearance`` Textual app and the embedded ``terok-tui``
screen); the producer-axis is fixed at shield, so "operator UI for
shield" rather than "any firewall console" is the right framing.
Task-name enrichment travels in the per-event ``dossier`` field —
the shield reader resolves the orchestrator's ``dossier.*`` OCI
annotations at emit time, so the notifier needs no runtime
inspector of its own.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os

from terok_clearance.client.subscriber import (
    ALL_NOTIFY_CATEGORIES,
    NOTIFY_BLOCKED,
    NOTIFY_VERDICT,
    EventSubscriber,
)
from terok_clearance.notifications.factory import create_notifier
from terok_clearance.notifications.protocol import Notifier
from terok_clearance.runtime.service import configure_logging, wait_for_shutdown_signal

_log = logging.getLogger(__name__)

#: Seconds granted to each teardown step during shutdown.  Prevents a
#: flaky session bus (unresponsive freedesktop notifications daemon,
#: hung varlink stream) from burning systemd's stop-sigterm deadline.
_CLEANUP_STEP_TIMEOUT_S = 2.0

#: Environment variable that picks which notification categories
#: render desktop popups.  Comma- (or whitespace-) separated list of
#: names from [`ALL_NOTIFY_CATEGORIES`][terok_clearance.client.subscriber.ALL_NOTIFY_CATEGORIES].
#: Unset → the default opt-in subset; empty string → silence every
#: category (still subscribes to the hub but never calls ``notify()``);
#: ``all`` → enable every recognised category.  Operators set this via
#: a ``systemctl --user edit terok-clearance-notifier`` drop-in so the
#: customisation survives ``terok setup`` reinstalls (the installer
#: only ever rewrites the main unit file, never the ``<unit>.service.d/``
#: directory).
NOTIFY_EVENTS_ENV = "TEROK_CLEARANCE_NOTIFY_EVENTS"

#: Default categories the notifier daemon subscribes to when the
#: environment variable is unset.  Tuned to cover the operator's
#: action loop ("here is a block, what's your verdict, here is the
#: result") while keeping passive shield/container chatter out of the
#: tray.
DEFAULT_NOTIFY_CATEGORIES: frozenset[str] = frozenset({NOTIFY_BLOCKED, NOTIFY_VERDICT})


def _parse_notify_categories(raw: str | None) -> frozenset[str]:
    """Resolve the env var into a category set, log unknowns, drop them.

    ``None`` (variable unset) falls back to
    [`DEFAULT_NOTIFY_CATEGORIES`][terok_clearance.notifier.app.DEFAULT_NOTIFY_CATEGORIES];
    the literal value ``all`` (case-insensitive) opts into every
    category; an empty string silences every category.  Tokens are
    comma- or whitespace-separated.  Unknown tokens are logged at
    WARNING and dropped — the operator gets a journald breadcrumb
    rather than a crashing notifier.
    """
    if raw is None:
        return DEFAULT_NOTIFY_CATEGORIES
    if raw.strip().lower() == "all":
        return ALL_NOTIFY_CATEGORIES
    tokens = {t.strip() for t in raw.replace(",", " ").split() if t.strip()}
    unknown = tokens - ALL_NOTIFY_CATEGORIES
    if unknown:
        _log.warning(
            "%s: ignoring unknown categories %s (recognised: %s)",
            NOTIFY_EVENTS_ENV,
            sorted(unknown),
            sorted(ALL_NOTIFY_CATEGORIES),
        )
    return frozenset(tokens & ALL_NOTIFY_CATEGORIES)


async def run_notifier() -> None:
    """Run the notifier until SIGINT/SIGTERM."""
    configure_logging()
    categories = _parse_notify_categories(os.environ.get(NOTIFY_EVENTS_ENV))
    _log.info("notify categories: %s", sorted(categories) or "<none>")
    notifier = await create_notifier("terok-clearance")
    subscriber = EventSubscriber(notifier, enabled_categories=categories)
    try:
        await subscriber.start()
    except Exception:
        _log.exception("clearance subscriber failed to connect to hub — exiting")
        with contextlib.suppress(Exception):
            await notifier.disconnect()
        raise SystemExit(1) from None

    _log.info("terok-clearance-notifier online")
    try:
        await wait_for_shutdown_signal()
    finally:
        await _teardown(subscriber, notifier)


async def _teardown(subscriber: EventSubscriber, notifier: Notifier) -> None:
    """Stop subscriber + disconnect notifier under per-step timeouts."""
    for name, coro in (
        ("subscriber", subscriber.stop()),
        ("notifier", notifier.disconnect()),
    ):
        try:
            await asyncio.wait_for(coro, timeout=_CLEANUP_STEP_TIMEOUT_S)
        except TimeoutError:
            _log.warning(
                "clearance-notifier shutdown: %s didn't finish within %gs",
                name,
                _CLEANUP_STEP_TIMEOUT_S,
            )
        except Exception as exc:  # noqa: BLE001 — shutdown must continue past any step
            _log.warning("clearance-notifier shutdown: %s failed (%s)", name, exc)


def main() -> None:  # pragma: no cover — CLI entry point
    """Systemd-unit ``ExecStart`` target — launches [`run_notifier`][terok_clearance.notifier.app.run_notifier] on an event loop."""
    asyncio.run(run_notifier())


if __name__ == "__main__":
    # Without this guard ``python -m terok_clearance.notifier.app`` under
    # systemd would import the module, define ``main``, and exit 0 without
    # running it — the notifier silently never started and every desktop
    # popup went missing.
    main()
