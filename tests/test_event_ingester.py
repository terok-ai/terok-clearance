# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the unix-socket event ingester that feeds the hub's signals."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from terok_dbus._event_ingester import EventIngester


async def _connect_and_send(path: Path, payload: bytes) -> None:
    """Open a client connection to *path* and write *payload* line-by-line."""
    _, writer = await asyncio.open_unix_connection(str(path))
    writer.write(payload)
    await writer.drain()
    writer.close()
    await writer.wait_closed()


class TestEventIngester:
    """The ingester decodes newline-delimited JSON and feeds its sink."""

    async def test_delivers_valid_event(self, tmp_path: Path) -> None:
        received: list[dict] = []

        async def sink(event: dict) -> None:
            received.append(event)

        socket_path = tmp_path / "events.sock"
        ingester = EventIngester(socket_path=socket_path, on_event=sink)
        await ingester.start()
        try:
            await _connect_and_send(
                socket_path,
                (json.dumps({"type": "container_started", "container": "c"}) + "\n").encode(),
            )
            for _ in range(20):
                if received:
                    break
                await asyncio.sleep(0.01)
        finally:
            await ingester.stop()

        assert received == [{"type": "container_started", "container": "c"}]

    async def test_ignores_blank_and_malformed_lines(self, tmp_path: Path) -> None:
        received: list[dict] = []

        async def sink(event: dict) -> None:
            received.append(event)

        socket_path = tmp_path / "events.sock"
        ingester = EventIngester(socket_path=socket_path, on_event=sink)
        await ingester.start()
        try:
            payload = b"\n" + b"not-json\n" + json.dumps({"type": "ok"}).encode() + b"\n"
            await _connect_and_send(socket_path, payload)
            for _ in range(20):
                if received:
                    break
                await asyncio.sleep(0.01)
        finally:
            await ingester.stop()

        assert received == [{"type": "ok"}]

    async def test_sink_exception_does_not_kill_ingester(self, tmp_path: Path) -> None:
        seen: list[dict] = []

        async def sink(event: dict) -> None:
            if event.get("type") == "boom":
                raise RuntimeError("sink crashed")
            seen.append(event)

        socket_path = tmp_path / "events.sock"
        ingester = EventIngester(socket_path=socket_path, on_event=sink)
        await ingester.start()
        try:
            payload = (
                json.dumps({"type": "boom"}).encode()
                + b"\n"
                + json.dumps({"type": "after-crash"}).encode()
                + b"\n"
            )
            await _connect_and_send(socket_path, payload)
            for _ in range(30):
                if seen:
                    break
                await asyncio.sleep(0.01)
        finally:
            await ingester.stop()

        assert seen == [{"type": "after-crash"}]

    async def test_stop_unlinks_socket(self, tmp_path: Path) -> None:
        socket_path = tmp_path / "events.sock"
        ingester = EventIngester(socket_path=socket_path, on_event=lambda _event: _noop())
        await ingester.start()
        assert socket_path.exists()
        await ingester.stop()
        assert not socket_path.exists()

    async def test_start_replaces_stale_socket_file(self, tmp_path: Path) -> None:
        socket_path = tmp_path / "events.sock"
        socket_path.write_text("leftover")  # pretend a crashed previous run

        ingester = EventIngester(socket_path=socket_path, on_event=lambda _event: _noop())
        await ingester.start()
        try:
            # Must still accept connections — not a regular file any more.
            await _connect_and_send(socket_path, b"{}\n")
        finally:
            await ingester.stop()


async def _noop() -> None:
    """Awaitable no-op sink used where the test doesn't inspect events."""
    return


@pytest.fixture
def anyio_backend() -> str:
    """Constrain any test that uses AnyIO to asyncio (no trio)."""
    return "asyncio"
