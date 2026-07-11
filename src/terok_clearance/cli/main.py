# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""CLI entry point for ``terok-clearance-hub`` — desktop notification tools.

Wires the [`COMMANDS`][terok_clearance.commands.COMMANDS] registry into
argparse via [`CommandTree`][terok_util.cli_types.CommandTree].  Passing
the process ``argv`` into
[`wire`][terok_util.cli_types.CommandTree.wire] makes dispatch lazy: only
the invoked verb's module is imported, so ``terok-clearance-hub notify``
pays for none of the hub / verdict / Textual verbs.
"""

import argparse
import sys

from terok_util import CommandTree

from terok_clearance.commands import COMMANDS


def main() -> None:
    """Entry point for ``terok-clearance-hub``."""
    argv = sys.argv[1:]
    parser = argparse.ArgumentParser(
        prog="terok-clearance-hub",
        description="Desktop notification tools for the terok ecosystem.",
    )
    CommandTree(COMMANDS).wire(parser, argv=argv)

    args = parser.parse_args(argv)
    if not hasattr(args, "_cmd"):
        parser.print_help()
        raise SystemExit(2)

    try:
        CommandTree.dispatch(args)
    except KeyboardInterrupt:
        raise SystemExit(130) from None


if __name__ == "__main__":
    main()
