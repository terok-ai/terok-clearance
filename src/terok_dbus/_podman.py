# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Resolve short podman container IDs to the human-readable container name.

Used by the hub to give the desktop notifier a friendly ``my-task``
string in place of ``fa0905d97a1c``.  Cached per-process so the hub
doesn't shell out for every ``ConnectionBlocked`` signal.
"""

from __future__ import annotations

import logging
import shutil
import subprocess  # nosec B404 — podman is a trusted host binary

_log = logging.getLogger(__name__)

_INSPECT_TIMEOUT_S = 5


class PodmanContainerNameResolver:
    """Cached ID → name lookup backed by ``podman inspect``.

    Callable: instances act as ``Callable[[str], str]``.  On miss, shells
    out to ``podman inspect --format '{{.Name}}' <id>`` and memoises the
    result for the lifetime of the resolver.  Returns an empty string on
    any failure so callers keep a usable fallback (the ID) in the rendered
    notification body.
    """

    def __init__(self) -> None:
        """Initialise with an empty cache."""
        self._cache: dict[str, str] = {}

    def __call__(self, container_id: str) -> str:
        """Return the container's human-readable name, or ``""`` on lookup failure."""
        if not container_id:
            return ""
        if (cached := self._cache.get(container_id)) is not None:
            return cached
        name = self._inspect(container_id)
        self._cache[container_id] = name
        return name

    @staticmethod
    def _inspect(container_id: str) -> str:
        """Shell out to ``podman inspect`` once, with timeout + soft-fail."""
        podman = shutil.which("podman")
        if not podman:
            _log.debug("podman not on PATH — name resolution unavailable")
            return ""
        try:
            # ``--`` guards against a hostile *container_id* that starts with
            # a dash being interpreted as a podman flag.  Container IDs never
            # naturally start with a dash but the public surface accepts
            # whatever the bus delivers; be defensive at the boundary.
            result = subprocess.run(  # nosec B603
                [podman, "inspect", "--format", "{{.Name}}", "--", container_id],
                check=False,
                capture_output=True,
                text=True,
                timeout=_INSPECT_TIMEOUT_S,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            _log.debug("podman inspect failed for %s: %s", container_id, exc)
            return ""
        if result.returncode != 0:
            _log.debug(
                "podman inspect %s returned %d: %s",
                container_id,
                result.returncode,
                result.stderr.strip(),
            )
            return ""
        return result.stdout.strip()
