# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""CLI entry point for ``terok-dbus`` — desktop notification tools.

Subcommands
-----------
notify      Send a one-shot desktop notification.
subscribe   Long-running bridge: Shield1/Clearance1 D-Bus signals → desktop notifications.
"""

import argparse
import asyncio
import logging
import signal
import sys

from terok_dbus import EventSubscriber, create_notifier


def _build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="terok-dbus",
        description="Desktop notification tools for the terok ecosystem.",
    )
    sub = parser.add_subparsers(dest="command")

    # ── notify ─────────────────────────────────────────────────────
    notify = sub.add_parser("notify", help="Send a one-shot desktop notification.")
    notify.add_argument("summary", help="Notification title")
    notify.add_argument("body", nargs="?", default="", help="Notification body text")
    notify.add_argument(
        "-t",
        "--timeout",
        type=int,
        default=-1,
        metavar="MS",
        help="Expiration timeout in milliseconds (-1 = server default)",
    )

    # ── subscribe ──────────────────────────────────────────────────
    sub.add_parser(
        "subscribe",
        help="Bridge Shield1/Clearance1 D-Bus signals to desktop notifications.",
    )

    return parser


async def _notify(args: argparse.Namespace) -> None:
    """Send a single notification and print its ID."""
    notifier = await create_notifier()
    try:
        notification_id = await notifier.notify(
            args.summary,
            args.body,
            timeout_ms=args.timeout,
        )
        print(notification_id)  # noqa: T201
    finally:
        await notifier.disconnect()


async def _subscribe() -> None:
    """Run the event subscriber until interrupted."""
    logging.basicConfig(
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        level=logging.INFO,
    )
    notifier = await create_notifier()
    subscriber = EventSubscriber(notifier)
    await subscriber.start()
    try:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGINT, signal.SIGTERM):
            loop.add_signal_handler(sig, stop.set)
        await stop.wait()
    finally:
        await subscriber.stop()
        await notifier.disconnect()


def main() -> None:
    """Entry point for ``terok-dbus``."""
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "notify":
        handler = _notify(args)
    elif args.command == "subscribe":
        handler = _subscribe()
    else:
        parser.print_help()
        sys.exit(2)

    try:
        asyncio.run(handler)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
