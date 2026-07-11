# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Command registry for terok-clearance.

Defines ``COMMANDS`` — the single source of truth consumed by both the
standalone ``terok-clearance-hub`` CLI and the terok integration layer.
Each entry is a **lazy root**: a
[`CommandDef`][terok_util.cli_types.CommandDef] carrying only ``name``,
``help``, and a ``source`` reference to the module that defines the
fully-populated verb.  Building ``COMMANDS`` therefore imports none of
the verb modules — [`CommandTree.wire`][terok_util.cli_types.CommandTree.wire]
resolves the one verb actually invoked, so ``terok-clearance-hub notify``
never loads the hub, verdict, or Textual stacks.
"""

from terok_util import CommandDef

COMMANDS: tuple[CommandDef, ...] = (
    CommandDef(
        name="notify",
        help="Send a one-shot desktop notification",
        source="terok_clearance.cli.verbs.notify:COMMAND",
    ),
    CommandDef(
        name="serve",
        help="Run the clearance hub (serves org.terok.Clearance1 varlink on a unix socket)",
        source="terok_clearance.cli.verbs.serve:COMMAND",
    ),
    CommandDef(
        name="serve-verdict",
        help="Run the verdict helper (serves org.terok.ClearanceVerdict1 for shield exec)",
        source="terok_clearance.cli.verbs.serve_verdict:COMMAND",
    ),
    CommandDef(
        name="clearance",
        help="Interactive terminal tool for shield clearance verdicts",
        source="terok_clearance.cli.verbs.clearance:COMMAND",
    ),
)
