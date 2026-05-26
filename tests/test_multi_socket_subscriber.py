# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for [`MultiSocketSubscriber`][terok_clearance.MultiSocketSubscriber] — fan-in.

The subscriber watches a filesystem glob (default
``$XDG_RUNTIME_DIR/terok/clearance/*.sock``), opens an
[`EventSubscriber`][terok_clearance.EventSubscriber] per matching path
on `start`, and rescans periodically so newly-started supervisors
join the merged stream without restart.  Tests inject a stub
``EventSubscriber`` factory so no real sockets need to exist.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from terok_clearance.client import subscriber as _subscriber_mod
from terok_clearance.client.subscriber import (
    ALL_NOTIFY_CATEGORIES,
    NOTIFY_BLOCKED,
    MultiSocketSubscriber,
)


@pytest.fixture
def stub_event_subscriber(
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[dict[str, MagicMock]]:
    """Patch ``EventSubscriber`` so created instances are recorded by socket path.

    Each constructed stub records its kwargs and exposes ``start`` /
    ``stop`` as AsyncMocks, so tests can assert which paths were
    subscribed, which categories propagated, and that ``stop`` ran on
    removal.
    """
    created: dict[str, MagicMock] = {}

    def _factory(notifier, *, socket_path: Path, enabled_categories=None) -> MagicMock:
        instance = MagicMock(name=f"EventSubscriber({socket_path})")
        instance.notifier = notifier
        instance.socket_path = socket_path
        instance.enabled_categories = enabled_categories
        instance.start = AsyncMock()
        instance.stop = AsyncMock()
        created[str(socket_path)] = instance
        return instance

    monkeypatch.setattr(_subscriber_mod, "EventSubscriber", _factory)
    yield created


def _write_socket(directory: Path, name: str) -> Path:
    """Create an empty placeholder file at *directory*/*name*; return its path."""
    path = directory / name
    path.touch()
    return path


class TestEmptyGlob:
    """No matching sockets is a valid starting state, not an error."""

    async def test_start_with_no_sockets_succeeds(
        self, tmp_path: Path, stub_event_subscriber: dict[str, MagicMock]
    ) -> None:
        glob_pattern = str(tmp_path / "*.sock")
        notifier = MagicMock()
        sub = MultiSocketSubscriber(notifier, socket_glob=glob_pattern)
        try:
            await sub.start()
            assert stub_event_subscriber == {}
        finally:
            await sub.stop()


class TestStartConnectsToExistingSockets:
    """Sockets that already exist at start get an `EventSubscriber` each."""

    async def test_each_socket_gets_a_subscriber(
        self, tmp_path: Path, stub_event_subscriber: dict[str, MagicMock]
    ) -> None:
        s1 = _write_socket(tmp_path, "a.sock")
        s2 = _write_socket(tmp_path, "b.sock")
        glob_pattern = str(tmp_path / "*.sock")
        notifier = MagicMock()

        sub = MultiSocketSubscriber(notifier, socket_glob=glob_pattern)
        try:
            await sub.start()
        finally:
            # Avoid waiting full rescan interval before teardown.
            await sub.stop()

        assert set(stub_event_subscriber.keys()) == {str(s1), str(s2)}
        for child in stub_event_subscriber.values():
            child.start.assert_awaited_once()


class TestAddNewSocketDuringRescan:
    """A socket that appears mid-run gets picked up by the next rescan tick."""

    async def test_new_socket_is_subscribed(
        self, tmp_path: Path, stub_event_subscriber: dict[str, MagicMock]
    ) -> None:
        glob_pattern = str(tmp_path / "*.sock")
        notifier = MagicMock()
        # Short rescan interval so the test doesn't hang for seconds.
        sub = MultiSocketSubscriber(notifier, socket_glob=glob_pattern, rescan_interval_s=0.01)
        try:
            await sub.start()
            assert stub_event_subscriber == {}

            # Create a socket mid-run and wait for the next rescan to land.
            new_sock = _write_socket(tmp_path, "new.sock")
            for _ in range(50):
                if str(new_sock) in stub_event_subscriber:
                    break
                await asyncio.sleep(0.02)
            assert str(new_sock) in stub_event_subscriber, "rescan never picked up new socket"
            stub_event_subscriber[str(new_sock)].start.assert_awaited_once()
        finally:
            await sub.stop()


class TestRemoveSocketDuringRescan:
    """A socket that disappears mid-run gets its child subscriber stopped."""

    async def test_disappeared_socket_subscriber_is_stopped(
        self, tmp_path: Path, stub_event_subscriber: dict[str, MagicMock]
    ) -> None:
        sock = _write_socket(tmp_path, "doomed.sock")
        glob_pattern = str(tmp_path / "*.sock")
        notifier = MagicMock()
        sub = MultiSocketSubscriber(notifier, socket_glob=glob_pattern, rescan_interval_s=0.01)
        try:
            await sub.start()
            assert str(sock) in stub_event_subscriber
            child = stub_event_subscriber[str(sock)]

            sock.unlink()
            for _ in range(50):
                if child.stop.await_count > 0:
                    break
                await asyncio.sleep(0.02)
            child.stop.assert_awaited_once()
        finally:
            await sub.stop()


class TestCategoryFilterPropagates:
    """The ``enabled_categories`` kwarg reaches every child subscriber."""

    async def test_categories_passed_to_each_child(
        self, tmp_path: Path, stub_event_subscriber: dict[str, MagicMock]
    ) -> None:
        sock = _write_socket(tmp_path, "filtered.sock")
        glob_pattern = str(tmp_path / "*.sock")
        notifier = MagicMock()
        categories = frozenset({NOTIFY_BLOCKED})

        sub = MultiSocketSubscriber(
            notifier,
            socket_glob=glob_pattern,
            enabled_categories=categories,
        )
        try:
            await sub.start()
            child = stub_event_subscriber[str(sock)]
            assert child.enabled_categories == categories
        finally:
            await sub.stop()

    async def test_none_categories_propagates_as_none(
        self, tmp_path: Path, stub_event_subscriber: dict[str, MagicMock]
    ) -> None:
        """``None`` opts into every category — the child receives ``None`` verbatim."""
        sock = _write_socket(tmp_path, "open.sock")
        glob_pattern = str(tmp_path / "*.sock")
        notifier = MagicMock()

        sub = MultiSocketSubscriber(notifier, socket_glob=glob_pattern)
        try:
            await sub.start()
            child = stub_event_subscriber[str(sock)]
            assert child.enabled_categories is None
        finally:
            await sub.stop()

    async def test_all_categories_set_propagates_verbatim(
        self, tmp_path: Path, stub_event_subscriber: dict[str, MagicMock]
    ) -> None:
        """Passing the full set is distinct from passing ``None``, by construction."""
        sock = _write_socket(tmp_path, "wide.sock")
        glob_pattern = str(tmp_path / "*.sock")
        notifier = MagicMock()

        sub = MultiSocketSubscriber(
            notifier,
            socket_glob=glob_pattern,
            enabled_categories=ALL_NOTIFY_CATEGORIES,
        )
        try:
            await sub.start()
            child = stub_event_subscriber[str(sock)]
            assert child.enabled_categories == ALL_NOTIFY_CATEGORIES
        finally:
            await sub.stop()


class TestStopShutsDownChildren:
    """`stop` cancels the rescan loop and awaits every child's ``stop``."""

    async def test_every_child_stop_runs(
        self, tmp_path: Path, stub_event_subscriber: dict[str, MagicMock]
    ) -> None:
        _write_socket(tmp_path, "x.sock")
        _write_socket(tmp_path, "y.sock")
        glob_pattern = str(tmp_path / "*.sock")
        notifier = MagicMock()

        sub = MultiSocketSubscriber(notifier, socket_glob=glob_pattern)
        await sub.start()
        children = list(stub_event_subscriber.values())
        await sub.stop()

        for child in children:
            child.stop.assert_awaited_once()

    async def test_double_stop_is_safe(
        self, tmp_path: Path, stub_event_subscriber: dict[str, MagicMock]
    ) -> None:
        """Calling `stop` twice must not raise — operators wire it through `finally`."""
        _write_socket(tmp_path, "z.sock")
        glob_pattern = str(tmp_path / "*.sock")
        notifier = MagicMock()

        sub = MultiSocketSubscriber(notifier, socket_glob=glob_pattern)
        await sub.start()
        await sub.stop()
        await sub.stop()  # second call must be a no-op


class TestSubscriberFailureIsSkipped:
    """A child subscriber whose `start` raises is logged and dropped — the rest survive."""

    async def test_failing_socket_does_not_block_others(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        good_sock = _write_socket(tmp_path, "good.sock")
        bad_sock = _write_socket(tmp_path, "bad.sock")
        glob_pattern = str(tmp_path / "*.sock")
        notifier = MagicMock()
        created: dict[str, MagicMock] = {}

        def _factory(notifier, *, socket_path: Path, enabled_categories=None) -> MagicMock:
            instance = MagicMock(name=f"EventSubscriber({socket_path})")
            instance.socket_path = socket_path
            if socket_path == bad_sock:
                instance.start = AsyncMock(side_effect=ConnectionError("nope"))
            else:
                instance.start = AsyncMock()
            instance.stop = AsyncMock()
            created[str(socket_path)] = instance
            return instance

        monkeypatch.setattr(_subscriber_mod, "EventSubscriber", _factory)

        sub = MultiSocketSubscriber(notifier, socket_glob=glob_pattern)
        try:
            await sub.start()
        finally:
            await sub.stop()

        # The good child remained subscribed; the bad one was dropped after the
        # failed start (its ``stop`` ran to release any partial transport state).
        assert created[str(good_sock)].start.await_count == 1
        assert created[str(bad_sock)].start.await_count == 1
        created[str(bad_sock)].stop.assert_awaited()


class TestDefaultSocketGlob:
    """The default glob derives from ``$XDG_RUNTIME_DIR``."""

    async def test_default_glob_uses_xdg_runtime_dir(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        stub_event_subscriber: dict[str, MagicMock],
    ) -> None:
        runtime = tmp_path / "runtime"
        (runtime / "terok" / "clearance").mkdir(parents=True)
        sock = _write_socket(runtime / "terok" / "clearance", "deadbeef.sock")
        monkeypatch.setenv("XDG_RUNTIME_DIR", str(runtime))
        notifier = MagicMock()

        sub = MultiSocketSubscriber(notifier)
        try:
            await sub.start()
        finally:
            await sub.stop()

        assert str(sock) in stub_event_subscriber
