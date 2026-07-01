# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""The [`ClearanceEvent`][terok_clearance.domain.events.ClearanceEvent] value type.

One flat dataclass carries every event kind the hub fans out to
subscribers.  Varlink IDL can't model sum types directly, so the
``type`` field discriminates and the remaining fields are populated
per-kind — the same pattern ``io.systemd.Resolve.Monitor`` uses.

The orchestrator-supplied identity bundle that rides on each event —
the [`Dossier`][terok_clearance.domain.events.Dossier] dict — is a
free-form string-to-string map: the wire format treats it as opaque
and the renderer only dereferences the small set of well-known keys
named by the ``DOSSIER_*`` constants below.

This module deliberately does **not** use ``from __future__ import
annotations``: `asyncvarlink` derives the varlink IDL by reading the raw
``dataclasses.fields(...).type`` of
[`ClearanceEvent`][terok_clearance.domain.events.ClearanceEvent], and under PEP
563 those become strings it can't resolve — so every field silently degrades to
a foreign ``object``. asyncvarlink ``>=0.3.2`` rejects foreign types outright,
turning that degradation into an import-time ``TypeError``. Keep the annotations
as real objects.
"""

from dataclasses import dataclass, field
from typing import Literal

#: Operator verdict on a blocked connection.  The full vocabulary lives
#: in [`VERDICT_ACTIONS`][terok_clearance.domain.events.VERDICT_ACTIONS]
#: — keep them in lock-step.
VerdictAction = Literal["allow", "deny"]

#: Wire-stable verdict values the hub accepts and the renderer expects.
#: Single source of truth for the action vocabulary: the hub validator,
#: the subscriber's notification buttons, and the terminal CLI all
#: derive their string set from this tuple.
VERDICT_ACTIONS: tuple[VerdictAction, ...] = ("allow", "deny")

#: Type alias for the orchestrator-supplied identity bundle resolved
#: by the shield reader at emit time.  Keys are the orchestrator's
#: contract — terok publishes ``project``, ``task``, ``name``, etc.
#: under the ``dossier.*`` OCI annotation namespace, but a non-terok
#: orchestrator may publish anything.  An empty dossier is the
#: shield-only-deployment shape; clients fall back to the bare
#: ``container`` short-id in that case.
Dossier = dict[str, str]

#: Project identifier within the orchestrator's namespace.  Combined
#: with ``DOSSIER_TASK`` to form the task triple shown in popup
#: bodies.  Empty when the orchestrator doesn't model projects.
DOSSIER_PROJECT = "project"

#: Task identifier within ``DOSSIER_PROJECT``.  Both keys must be
#: populated for the renderer to switch from a "Container: …" body
#: to a "Task: project/task · name" body.
DOSSIER_TASK = "task"

#: Human-readable label for the task or container — whatever the
#: orchestrator considers the friendly name.  For terok this tracks
#: the in-flight task name (renamable; resolved on every emit from
#: the meta-path JSON file).
DOSSIER_NAME = "name"

#: Container name as the runtime sees it.  Optional — in practice
#: equal to ``DOSSIER_NAME`` for terok, but the orchestrator may
#: set them separately (e.g. a stable container name plus a
#: human-edited task name).
DOSSIER_CONTAINER_NAME = "container_name"


@dataclass
class ClearanceEvent:
    """One event fanned out to every ``Subscribe()`` caller.

    ``type`` + ``container`` are always populated; the remaining fields
    are filled in per-kind and default to zero-values otherwise.

    Known values of ``type`` (additional fields beyond ``container``):

    * ``connection_blocked`` — ``request_id``, ``dest``, ``port``,
      ``proto``, ``domain``, ``dossier``.  Requires an operator verdict.
    * ``verdict_applied`` — ``request_id``, ``action``, ``ok``.
    * ``container_started`` — ``dossier``.
    * ``container_exited`` — ``reason``, ``dossier``.
    * ``shield_up`` / ``shield_down`` / ``shield_disengaged`` — ``dossier``.

    Unknown values are forwarded unchanged so the wire format can grow
    without breaking clients pinned to older schemas.
    """

    type: str
    container: str
    request_id: str = ""
    dest: str = ""
    port: int = 0
    proto: int = 0
    domain: str = ""
    action: str = ""
    ok: bool = False
    reason: str = ""
    dossier: Dossier = field(default_factory=dict)
