# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""The [`ClearanceEvent`][terok_clearance.domain.events.ClearanceEvent] value type.

One flat dataclass carries every event kind the hub fans out to
subscribers.  Varlink IDL can't model sum types directly, so the
``type`` field discriminates and the remaining fields are populated
per-kind — the same pattern ``io.systemd.Resolve.Monitor`` uses.
"""

from __future__ import annotations

from dataclasses import dataclass, field


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
    * ``shield_up`` / ``shield_down`` / ``shield_down_all`` — ``dossier``.

    Unknown values are forwarded unchanged so the wire format can grow
    without breaking clients pinned to older schemas.

    ``dossier`` carries the orchestrator-supplied identity bundle that the
    shield's per-container reader resolved at emit time — the keys are
    whatever the orchestrator publishes under its ``dossier.*`` OCI
    annotation namespace (``project``, ``task``, ``name``, …).  Empty for
    shield-only deployments where no orchestrator participates; clients
    must fall back to ``container`` (the short ID) in that case.
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
    dossier: dict[str, str] = field(default_factory=dict)
