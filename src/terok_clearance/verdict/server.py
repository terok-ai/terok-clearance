# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""The verdict helper — a minimal varlink server wrapping ``terok-shield``.

One process, one socket, one method (``Apply``).  Runs as its own
systemd user unit (``terok-clearance-verdict.service``) so the
companion hub unit can take full seccomp + mount-ns hardening without
tripping the kernel's NNP requirement and SELinux's denial of the
``unconfined_t → container_runtime_t`` transition that rootless podman
needs every time shield exec's ``podman unshare nsenter nft``.

Stateless: no authz decisions, no request-id binding, no fan-out.
The hub already validated the verdict triple before forwarding; the
helper exists solely to isolate the hostile exec path from the
hardened receive path.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import sys
from pathlib import Path

from asyncvarlink import VarlinkInterfaceRegistry, VarlinkUnixServer, create_unix_server
from asyncvarlink.serviceinterface import VarlinkServiceInterface

from terok_clearance.verdict.interface import Verdict1Interface
from terok_clearance.verdict.socket import default_verdict_socket_path
from terok_clearance.wire.socket import bind_hardened

_log = logging.getLogger(__name__)


# ── ``terok-shield allow|deny`` exec path ─────────────────────────
#
# The exec path lives here because the verdict helper process is the
# only thing that ever calls it — and the one thing the hub *cannot*
# do under any real systemd hardening.  ``podman unshare nsenter nft``
# (which shield exec's under the covers) requires the hub's user+mount
# namespace to match the pause process's, and any seccomp-based or
# mount-ns-isolating unit directive breaks that setns.  The verdict
# helper runs unhardened; the hub, freed from this exec, runs under
# ``NoNewPrivileges=yes`` + ``@system-service``.

#: Upper bound on a single ``terok-shield allow|deny`` invocation.  Shield
#: holds an nft lock and can also block on a slow podman pause; clients
#: have their own reply timeout, so failing-fast here surfaces the real
#: outcome instead of letting the RPC call hang.
_SHIELD_CLI_TIMEOUT_S = 10.0

#: Cap stderr bytes we forward back to the hub.  Desktop popups can't
#: render multi-kilobyte bodies; clients truncate too.  Prevents a
#: shield crash dump from travelling end-to-end as a varlink error
#: parameter.
_STDERR_CAP_BYTES = 512


async def run_shield(
    shield_binary: str | None, container: str, dest: str, action: str
) -> tuple[bool, str]:
    """Invoke ``terok-shield <action> <container> <dest>``; return ``(ok, snippet)``.

    Bounded by `_SHIELD_CLI_TIMEOUT_S`.  Spawn errors, non-zero
    exit, and timeouts all fold into ``(False, reason)`` so callers
    see one shape regardless of how shield misbehaved.  ``snippet``
    is capped at `_STDERR_CAP_BYTES`.
    """
    if not shield_binary:
        return False, "terok-shield not found on PATH"
    try:
        proc = await asyncio.create_subprocess_exec(
            shield_binary,
            action,
            container,
            dest,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError as exc:
        _log.error("failed to spawn terok-shield: %s", exc)
        return False, f"spawn failed: {exc}"
    try:
        _, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=_SHIELD_CLI_TIMEOUT_S)
    except TimeoutError:
        proc.kill()
        with contextlib.suppress(Exception):
            await proc.communicate()
        _log.warning("shield %s timed out after %gs", action, _SHIELD_CLI_TIMEOUT_S)
        return False, f"timed out after {_SHIELD_CLI_TIMEOUT_S}s"
    snippet = (stderr_bytes[:_STDERR_CAP_BYTES] or b"").decode(errors="replace").strip()
    ok = proc.returncode == 0
    if not ok:
        _log.warning("shield %s failed: %s", action, snippet)
    return ok, snippet


def find_shield_binary() -> str | None:
    """Locate ``terok-shield`` — sibling venv first, then PATH, then ``None``.

    The sibling check handles the pipx / poetry case where terok-shield
    ships in the same venv as terok-clearance; we prefer it over PATH
    so a shell-rc ``PATH`` shim can't redirect verdicts through a
    different installation.  ``is_file`` alone would happily return a
    non-executable artifact, so the exec-bit check prevents a broken
    install from failing every verdict instead of falling through to
    PATH's working copy.
    """
    sibling = Path(sys.executable).parent / "terok-shield"
    if sibling.is_file() and os.access(sibling, os.X_OK):
        return str(sibling)
    return shutil.which("terok-shield")


class VerdictServer:
    """Per-process wrapper around the ``Apply`` varlink interface.

    The hub is the only legitimate client; ``SO_PEERCRED`` on the unix
    socket rejects peers with a different UID, and
    [`bind_hardened`][terok_clearance.wire.socket.bind_hardened] leaves the
    socket mode ``0600`` for the lifetime of the server.
    """

    def __init__(
        self,
        *,
        socket_path: Path | None = None,
        shield_binary: str | None = None,
    ) -> None:
        """Configure the socket + shield executable path."""
        self._socket_path = socket_path or default_verdict_socket_path()
        self._shield_binary = shield_binary or find_shield_binary()
        self._server: VarlinkUnixServer | None = None

    async def start(self) -> None:
        """Bind the varlink server and start accepting hub verdict calls."""
        registry = VarlinkInterfaceRegistry()
        registry.register_interface(Verdict1Interface(apply_verdict=self._apply))
        registry.register_interface(
            VarlinkServiceInterface(
                vendor="terok",
                product="terok-clearance-verdict",
                version=_own_version(),
                url="https://github.com/terok-ai/terok-clearance",
                registry=registry,
            )
        )

        async def _factory(path: str) -> object:
            return await create_unix_server(registry.protocol_factory, path=path)

        self._server = await bind_hardened(_factory, self._socket_path, "verdict")
        _log.info("verdict helper online at %s", self._socket_path)

    async def stop(self) -> None:
        """Close the varlink server; existing in-flight Apply calls finish first."""
        if self._server is None:
            return
        self._server.close()
        with contextlib.suppress(AttributeError):
            self._server.close_clients()
        with contextlib.suppress(TimeoutError, Exception):
            await asyncio.wait_for(self._server.wait_closed(), timeout=1.0)
        self._server = None

    async def _apply(self, container: str, dest: str, action: str) -> tuple[bool, str]:
        """Forward one verdict to [`run_shield`][terok_clearance.verdict.server.run_shield], no validation."""
        return await run_shield(self._shield_binary, container, dest, action)


async def serve() -> None:
    """Bring the verdict helper online and stay up until SIGINT/SIGTERM.

    Mirrors [`terok_clearance.hub.server.serve`][terok_clearance.hub.server.serve] so the CLI layer
    can dispatch both entrypoints through the same ``asyncio.run``
    pattern.
    """
    from terok_clearance.runtime.service import configure_logging, wait_for_shutdown_signal

    configure_logging()
    server = VerdictServer()
    await server.start()
    try:
        await wait_for_shutdown_signal()
    finally:
        await server.stop()


def _own_version() -> str:
    """Return the package version for varlink ``GetInfo`` — best-effort."""
    try:
        from importlib.metadata import version

        return version("terok-clearance")
    except Exception:  # pragma: no cover — only hits if metadata is missing
        return "0.0.0"
