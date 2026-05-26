# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the ``socket_context`` plumbing in
[`bind_hardened`][terok_clearance.wire.socket.bind_hardened].

The callable lets a caller wrap the ``await factory(...)`` step in a
context manager (e.g. SELinux ``setsockcreatecon``) without making
this package aware of the labelling mechanism.
"""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path

import pytest

from terok_clearance.wire.socket import bind_hardened


class _Probe:
    """Minimal call-order recorder for the context manager + factory."""

    def __init__(self) -> None:
        self.events: list[str] = []

    def context(self):  # noqa: ANN201 — test helper
        probe = self

        @contextmanager
        def _cm():
            probe.events.append("enter")
            try:
                yield
            finally:
                probe.events.append("exit")

        return _cm()


@pytest.mark.asyncio
async def test_socket_context_wraps_factory_call(tmp_path: Path) -> None:
    """The context is entered before and exited after the factory awaits."""
    probe = _Probe()
    import socket as _socket

    async def _factory(path: str) -> _socket.socket:
        probe.events.append(f"bind:{Path(path).name}")
        s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        s.bind(path)
        return s

    sock_path = tmp_path / "probe.sock"
    server = await bind_hardened(_factory, sock_path, "probe", socket_context=probe.context)
    try:
        assert probe.events == ["enter", f"bind:{sock_path.name}", "exit"]
    finally:
        server.close()


@pytest.mark.asyncio
async def test_no_socket_context_is_a_no_op(tmp_path: Path) -> None:
    """Omitting ``socket_context`` leaves the default no-op behaviour intact."""
    import socket as _socket

    async def _factory(path: str) -> _socket.socket:
        s = _socket.socket(_socket.AF_UNIX, _socket.SOCK_STREAM)
        s.bind(path)
        return s

    sock_path = tmp_path / "noctx.sock"
    server = await bind_hardened(_factory, sock_path, "noctx")
    try:
        assert sock_path.is_socket()
    finally:
        server.close()
