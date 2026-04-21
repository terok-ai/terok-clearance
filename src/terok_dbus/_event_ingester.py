# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Unix-socket ingester that relays container events onto the session bus.

Per-container NFLOG readers live in ``NS_ROOTLESS`` (the rootless-podman
user namespace that owns the container netns).  From there, the session
``dbus-daemon``'s ``SO_PEERCRED`` check rejects their connection attempts
— even when ``DBUS_SESSION_BUS_ADDRESS`` points at the right socket.

The hub runs in the host user namespace, so it *can* reach the session
bus.  :class:`EventIngester` gives the readers a pipe to cross: it owns
a unix socket that accepts line-delimited JSON events from any local
connection, decodes them, and calls the matching :class:`ShieldHub`
signal methods on the bus — where emission works.

One socket per hub, one hub per user session.  Readers reconnect on
their own if the hub restarts; the hub tolerates disconnected readers
without logging.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
from collections.abc import Awaitable, Callable
from pathlib import Path

_log = logging.getLogger(__name__)

_SOCKET_BASENAME = "terok-shield-events.sock"


def default_socket_path() -> Path:
    """Return the canonical ingester path under ``$XDG_RUNTIME_DIR``."""
    xdg = os.environ.get("XDG_RUNTIME_DIR")
    if not xdg:
        xdg = f"/run/user/{os.getuid()}"
    return Path(xdg) / _SOCKET_BASENAME


class EventIngester:
    """Accepts JSON event lines from container readers and forwards to the hub.

    Keeps ownership of one AF_UNIX listener and a set of accepted-connection
    handler tasks.  Socket file mode is 0600: only the hub's running user
    can read or write to it, matching the session bus's own ACL model.
    """

    def __init__(
        self,
        *,
        socket_path: Path,
        on_event: Callable[[dict], Awaitable[None]],
    ) -> None:
        """Bind the ingester to a filesystem path and a sink coroutine.

        Args:
            socket_path: Where the listening AF_UNIX socket will live.  The
                path is unlinked first if a stale file exists, so a crashed
                previous run doesn't deadlock startup.
            on_event: Coroutine the ingester awaits once per parsed event.
                Expected to emit the corresponding D-Bus signal; exceptions
                raised here are logged and swallowed so one bad event can't
                tear down the ingester.
        """
        self._socket_path = socket_path
        self._on_event = on_event
        self._server: asyncio.AbstractServer | None = None
        self._clients: set[asyncio.Task] = set()

    async def start(self) -> None:
        """Bind the socket and start accepting connections in the background."""
        with contextlib.suppress(FileNotFoundError):
            self._socket_path.unlink()
        self._socket_path.parent.mkdir(parents=True, exist_ok=True)
        self._server = await asyncio.start_unix_server(
            self._handle_client, path=str(self._socket_path)
        )
        os.chmod(self._socket_path, 0o600)  # noqa: S103
        _log.info("event ingester listening on %s", self._socket_path)

    async def stop(self) -> None:
        """Close the server and await any in-flight client tasks."""
        # Cancel client handlers *before* awaiting ``wait_closed()``: from
        # Python 3.12.1 onwards the server tracks active connections and
        # ``wait_closed()`` blocks until every one of them returns.  If we
        # waited first we'd deadlock against our own accepted tasks.
        if self._server is not None:
            self._server.close()
        for task in list(self._clients):
            task.cancel()
        for task in list(self._clients):
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        if self._server is not None:
            await self._server.wait_closed()
            self._server = None
        with contextlib.suppress(FileNotFoundError):
            self._socket_path.unlink()

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        """Read newline-delimited JSON events until the peer disconnects."""
        task = asyncio.current_task()
        if task is not None:
            self._clients.add(task)
        try:
            while True:
                line = await reader.readline()
                if not line:
                    return
                await self._dispatch(line)
        finally:
            if task is not None:
                self._clients.discard(task)
            writer.close()
            with contextlib.suppress(Exception):
                await writer.wait_closed()

    async def _dispatch(self, raw: bytes) -> None:
        """Decode one line and forward it to the caller-supplied sink."""
        text = raw.strip()
        if not text:
            return
        try:
            event = json.loads(text)
        except json.JSONDecodeError:
            _log.warning("ingester: dropping malformed JSON: %r", text[:120])
            return
        if not isinstance(event, dict):
            _log.warning("ingester: dropping non-object event: %r", text[:120])
            return
        try:
            await self._on_event(event)
        except Exception as exc:  # noqa: BLE001
            _log.warning("ingester: sink raised %s on %r", exc, event)
