# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the clearance notifier daemon.

Covers the glue between the subscriber and the notification backend —
the parts of the daemon that are reachable without a live session
D-Bus or varlink hub.  Identity resolution lives at the shield reader
now (per-event ``dossier``), so the daemon owns no inspector wiring of
its own and there's nothing here to test on that axis.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from terok_clearance.client.subscriber import (
    ALL_NOTIFY_CATEGORIES,
    NOTIFY_BLOCKED,
    NOTIFY_CONTAINER_STARTED,
    NOTIFY_SHIELD_DOWN,
    NOTIFY_VERDICT,
)
from terok_clearance.notifier import app as notifier_app

# ── _teardown ─────────────────────────────────────────────


async def test_teardown_runs_subscriber_stop_then_notifier_disconnect() -> None:
    """Happy path: both cleanup steps await, in order, within the timeout."""
    order: list[str] = []

    async def _record(tag: str) -> None:
        order.append(tag)

    subscriber = MagicMock()
    subscriber.stop = lambda: _record("subscriber")
    notifier = MagicMock()
    notifier.disconnect = lambda: _record("notifier")

    await notifier_app._teardown(subscriber, notifier)
    assert order == ["subscriber", "notifier"]


async def test_teardown_logs_timeout_but_runs_disconnect() -> None:
    """A stuck ``stop()`` must not starve ``disconnect()`` of its cleanup."""
    disconnected = False

    async def _hang() -> None:
        await asyncio.sleep(10)

    async def _fast_disconnect() -> None:
        nonlocal disconnected
        disconnected = True

    subscriber = MagicMock()
    subscriber.stop = _hang
    notifier = MagicMock()
    notifier.disconnect = _fast_disconnect

    # Patch the per-step timeout to something short so the test stays fast.
    with patch.object(notifier_app, "_CLEANUP_STEP_TIMEOUT_S", 0.01):
        await notifier_app._teardown(subscriber, notifier)
    assert disconnected is True


async def test_teardown_logs_exception_but_runs_disconnect() -> None:
    """A raising ``stop()`` must not take ``disconnect()`` down with it."""
    disconnected = False

    async def _boom() -> None:
        raise RuntimeError("varlink went sideways")

    async def _fast_disconnect() -> None:
        nonlocal disconnected
        disconnected = True

    subscriber = MagicMock()
    subscriber.stop = _boom
    notifier = MagicMock()
    notifier.disconnect = _fast_disconnect

    await notifier_app._teardown(subscriber, notifier)
    assert disconnected is True


# ── run_notifier ──────────────────────────────────────────


async def test_run_notifier_happy_path_returns_on_shutdown_signal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Subscriber starts, shutdown signal fires, teardown runs, no SystemExit."""
    notifier = MagicMock(name="notifier")
    notifier.disconnect = AsyncMock()
    subscriber = MagicMock(name="subscriber")
    subscriber.start = AsyncMock()
    subscriber.stop = AsyncMock()

    monkeypatch.setattr(notifier_app, "configure_logging", lambda: None)
    monkeypatch.setattr(notifier_app, "create_notifier", AsyncMock(return_value=notifier))
    monkeypatch.setattr(notifier_app, "EventSubscriber", MagicMock(return_value=subscriber))
    monkeypatch.setattr(notifier_app, "wait_for_shutdown_signal", AsyncMock(return_value=None))

    await notifier_app.run_notifier()
    subscriber.start.assert_awaited_once()
    subscriber.stop.assert_awaited_once()
    notifier.disconnect.assert_awaited_once()


async def test_run_notifier_exits_when_subscriber_start_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed subscriber start → ``SystemExit(1)`` after disconnecting the notifier."""
    notifier = MagicMock(name="notifier")
    notifier.disconnect = AsyncMock()
    subscriber = MagicMock(name="subscriber")
    subscriber.start = AsyncMock(side_effect=RuntimeError("hub unreachable"))

    monkeypatch.setattr(notifier_app, "configure_logging", lambda: None)
    monkeypatch.setattr(notifier_app, "create_notifier", AsyncMock(return_value=notifier))
    monkeypatch.setattr(notifier_app, "EventSubscriber", MagicMock(return_value=subscriber))

    with pytest.raises(SystemExit) as exc_info:
        await notifier_app.run_notifier()
    assert exc_info.value.code == 1
    # Even on a failed start, the notifier gets cleaned up so the
    # session bus connection doesn't leak.
    notifier.disconnect.assert_awaited_once()


async def test_run_notifier_swallows_notifier_disconnect_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A disconnect failure on the error path must not mask the original SystemExit."""
    notifier = MagicMock(name="notifier")
    notifier.disconnect = AsyncMock(side_effect=RuntimeError("dbus drop"))
    subscriber = MagicMock(name="subscriber")
    subscriber.start = AsyncMock(side_effect=RuntimeError("hub gone"))

    monkeypatch.setattr(notifier_app, "configure_logging", lambda: None)
    monkeypatch.setattr(notifier_app, "create_notifier", AsyncMock(return_value=notifier))
    monkeypatch.setattr(notifier_app, "EventSubscriber", MagicMock(return_value=subscriber))

    with pytest.raises(SystemExit):
        await notifier_app.run_notifier()


# ── _parse_notify_categories ─────────────────────────────


class TestParseNotifyCategories:
    """``TEROK_CLEARANCE_NOTIFY_EVENTS`` is the operator's category knob."""

    def test_unset_returns_default_subset(self) -> None:
        """Variable unset → the curated interactive default (blocked + verdict)."""
        assert notifier_app._parse_notify_categories(None) == notifier_app.DEFAULT_NOTIFY_CATEGORIES
        assert frozenset({NOTIFY_BLOCKED, NOTIFY_VERDICT}) == notifier_app.DEFAULT_NOTIFY_CATEGORIES

    def test_empty_string_silences_every_popup(self) -> None:
        """Operators can mute the daemon entirely with ``Environment="…=" ``."""
        assert notifier_app._parse_notify_categories("") == frozenset()

    def test_all_keyword_enables_every_category(self) -> None:
        """A literal ``all`` (case-insensitive) opts back into the historical chatter."""
        assert notifier_app._parse_notify_categories("all") == ALL_NOTIFY_CATEGORIES
        assert notifier_app._parse_notify_categories("ALL") == ALL_NOTIFY_CATEGORIES

    def test_comma_and_whitespace_separators_both_parse(self) -> None:
        """Both shell-comma and whitespace tokens land in the same set."""
        assert notifier_app._parse_notify_categories("blocked,verdict") == frozenset(
            {NOTIFY_BLOCKED, NOTIFY_VERDICT}
        )
        assert notifier_app._parse_notify_categories("blocked verdict") == frozenset(
            {NOTIFY_BLOCKED, NOTIFY_VERDICT}
        )
        assert notifier_app._parse_notify_categories(" blocked ,  shield_down ") == frozenset(
            {NOTIFY_BLOCKED, NOTIFY_SHIELD_DOWN}
        )

    def test_unknown_tokens_are_dropped_with_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Typos don't crash the daemon — they're logged and discarded."""
        with caplog.at_level("WARNING"):
            result = notifier_app._parse_notify_categories("blocked,bogus,container_started")
        assert result == frozenset({NOTIFY_BLOCKED, NOTIFY_CONTAINER_STARTED})
        assert "bogus" in caplog.text


async def test_run_notifier_passes_category_filter_to_subscriber(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The env-var subset reaches ``EventSubscriber.__init__`` as ``enabled_categories``."""
    notifier = MagicMock(name="notifier")
    notifier.disconnect = AsyncMock()
    subscriber = MagicMock(name="subscriber")
    subscriber.start = AsyncMock()
    subscriber.stop = AsyncMock()
    event_subscriber_ctor = MagicMock(return_value=subscriber)

    monkeypatch.setenv(notifier_app.NOTIFY_EVENTS_ENV, "blocked verdict")
    monkeypatch.setattr(notifier_app, "configure_logging", lambda: None)
    monkeypatch.setattr(notifier_app, "create_notifier", AsyncMock(return_value=notifier))
    monkeypatch.setattr(notifier_app, "EventSubscriber", event_subscriber_ctor)
    monkeypatch.setattr(notifier_app, "wait_for_shutdown_signal", AsyncMock(return_value=None))

    await notifier_app.run_notifier()
    event_subscriber_ctor.assert_called_once()
    kwargs = event_subscriber_ctor.call_args.kwargs
    assert kwargs["enabled_categories"] == frozenset({NOTIFY_BLOCKED, NOTIFY_VERDICT})
