# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for [`EventSubscriber`][terok_clearance.EventSubscriber] — the notification-rendering layer.

Exercises the dispatch + state machine in isolation by mocking the
[`ClearanceClient`][terok_clearance.ClearanceClient] transport.  Real varlink round-trips live in
``test_client.py``; here we feed [`ClearanceEvent`][terok_clearance.ClearanceEvent] instances
straight into `EventSubscriber._on_event` and inspect the
notifier it drives.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from unittest.mock import AsyncMock, MagicMock

import pytest

from terok_clearance.client.subscriber import (
    _HINT_BLOCK_PENDING,
    _HINT_CONFIRMATION,
    _HINT_LIFECYCLE,
    _HINT_SECURITY_ALERT,
    ALL_NOTIFY_CATEGORIES,
    NOTIFY_BLOCKED,
    NOTIFY_CONTAINER_STARTED,
    NOTIFY_SHIELD_DOWN,
    NOTIFY_VERDICT,
    EventSubscriber,
)
from terok_clearance.domain.events import ClearanceEvent

from .conftest import CONTAINER, DEST_IP, DOMAIN

# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def mock_notifier() -> AsyncMock:
    """Notifier stub — notify() returns a monotonic id, close() is a no-op."""
    notifier = AsyncMock()
    notifier.notify = AsyncMock(return_value=42)
    notifier.on_action = AsyncMock()
    notifier.close = AsyncMock()
    return notifier


@pytest.fixture
def subscriber(mock_notifier: AsyncMock) -> EventSubscriber:
    """A subscriber with a mocked client — no actual varlink traffic."""
    client = MagicMock()
    client.start = AsyncMock()
    client.stop = AsyncMock()
    client.verdict = AsyncMock(return_value=True)
    return EventSubscriber(mock_notifier, client=client)


def _blocked(
    request_id: str = f"{CONTAINER}:1",
    *,
    container: str = CONTAINER,
    dest: str = DEST_IP,
    domain: str = DOMAIN,
    port: int = 443,
    proto: int = 6,
    dossier: dict[str, str] | None = None,
) -> ClearanceEvent:
    """Build a ``connection_blocked`` event with sensible defaults."""
    return ClearanceEvent(
        type="connection_blocked",
        container=container,
        request_id=request_id,
        dest=dest,
        port=port,
        proto=proto,
        domain=domain,
        dossier=dossier or {},
    )


# ── connection_blocked ────────────────────────────────────────────────


class TestConnectionBlocked:
    """First-block rendering and its per-event side effects."""

    @pytest.mark.asyncio
    async def test_first_block_fires_a_prompt(
        self, subscriber: EventSubscriber, mock_notifier: AsyncMock
    ) -> None:
        """Summary + body + hints match the spec for a brand-new block."""
        await subscriber._on_event(_blocked())
        mock_notifier.notify.assert_awaited_once()
        call = mock_notifier.notify.await_args
        assert call.args[0] == f"Blocked: {DOMAIN}:443"
        assert "TCP" in call.args[1]
        assert call.kwargs["hints"] is _HINT_BLOCK_PENDING
        assert call.kwargs["timeout_ms"] == 0
        # No replaces_id on the first block (freedesktop spec: 0 = fresh).
        assert call.kwargs.get("replaces_id", 0) == 0

    @pytest.mark.asyncio
    async def test_first_block_records_pending_state(self, subscriber: EventSubscriber) -> None:
        await subscriber._on_event(_blocked())
        assert f"{CONTAINER}:1" in subscriber._pending

    @pytest.mark.asyncio
    async def test_empty_target_event_is_dropped(
        self, subscriber: EventSubscriber, mock_notifier: AsyncMock
    ) -> None:
        """Malformed event (empty dest AND domain) produces no notification."""
        await subscriber._on_event(_blocked(dest="", domain=""))
        mock_notifier.notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_second_block_same_target_reuses_notification(
        self, subscriber: EventSubscriber, mock_notifier: AsyncMock
    ) -> None:
        """A repeat block for the same (container, target) updates the live popup."""
        await subscriber._on_event(_blocked(f"{CONTAINER}:1"))
        await subscriber._on_event(_blocked(f"{CONTAINER}:2"))
        assert mock_notifier.notify.await_count == 2
        second = mock_notifier.notify.await_args_list[1]
        assert second.kwargs["replaces_id"] == 42
        assert "Blocked 2 times since" in second.args[1]
        # Only the latest request_id survives in _pending.
        assert f"{CONTAINER}:1" not in subscriber._pending
        assert f"{CONTAINER}:2" in subscriber._pending

    @pytest.mark.asyncio
    async def test_different_target_creates_distinct_popup(
        self, subscriber: EventSubscriber, mock_notifier: AsyncMock
    ) -> None:
        """Blocks on distinct domains never dedup."""
        await subscriber._on_event(_blocked(f"{CONTAINER}:1", domain="a.example.net"))
        await subscriber._on_event(_blocked(f"{CONTAINER}:2", domain="b.example.net"))
        # Both get fresh notifications (replaces_id==0 on both).
        assert mock_notifier.notify.await_count == 2
        for call in mock_notifier.notify.await_args_list:
            assert call.kwargs.get("replaces_id", 0) == 0


# ── verdict_applied ───────────────────────────────────────────────────


class TestVerdictApplied:
    """Outcome rendering + in-place replacement via replaces_id."""

    @pytest.mark.asyncio
    async def test_success_renders_confirmation(
        self, subscriber: EventSubscriber, mock_notifier: AsyncMock
    ) -> None:
        await subscriber._on_event(_blocked())
        mock_notifier.notify.reset_mock()
        await subscriber._on_event(
            ClearanceEvent(
                type="verdict_applied",
                container=CONTAINER,
                request_id=f"{CONTAINER}:1",
                action="allow",
                ok=True,
            )
        )
        call = mock_notifier.notify.await_args
        assert call.args[0] == f"Allowed: {DOMAIN}"
        assert call.kwargs["hints"] is _HINT_CONFIRMATION
        assert call.kwargs["replaces_id"] == 42
        # Pending entry released on verdict.
        assert f"{CONTAINER}:1" not in subscriber._pending

    @pytest.mark.asyncio
    async def test_failure_renders_security_alert(
        self, subscriber: EventSubscriber, mock_notifier: AsyncMock
    ) -> None:
        """ok=false flips to critical hints + 'failed' verb."""
        await subscriber._on_event(_blocked())
        mock_notifier.notify.reset_mock()
        await subscriber._on_event(
            ClearanceEvent(
                type="verdict_applied",
                container=CONTAINER,
                request_id=f"{CONTAINER}:1",
                action="allow",
                ok=False,
            )
        )
        call = mock_notifier.notify.await_args
        assert call.args[0] == f"Allow failed: {DOMAIN}"
        assert call.kwargs["hints"] is _HINT_SECURITY_ALERT

    @pytest.mark.asyncio
    async def test_no_pending_is_silent(
        self, subscriber: EventSubscriber, mock_notifier: AsyncMock
    ) -> None:
        """A verdict_applied for a request we didn't see produces no popup."""
        await subscriber._on_event(
            ClearanceEvent(
                type="verdict_applied",
                container=CONTAINER,
                request_id="ghost:9",
                action="allow",
                ok=True,
            )
        )
        mock_notifier.notify.assert_not_called()


# ── shield_down / shield_up ───────────────────────────────────────────


class TestShieldState:
    """Persistent ShieldDown alerts get retired on ShieldUp."""

    @pytest.mark.parametrize(
        ("member", "expected_title", "body_hint"),
        [
            ("shield_down", "Shield down: ", "bypassed"),
            ("shield_disengaged", "Shield disengaged: ", "disengaged"),
        ],
    )
    @pytest.mark.asyncio
    async def test_shield_down_posts_security_alert(
        self,
        subscriber: EventSubscriber,
        mock_notifier: AsyncMock,
        member: str,
        expected_title: str,
        body_hint: str,
    ) -> None:
        await subscriber._on_event(ClearanceEvent(type=member, container=CONTAINER))
        # The _notify_shield_down dispatch is scheduled as a background task;
        # yield the loop so it gets a chance to run.
        for _ in range(3):
            await asyncio.sleep(0)
        alert_calls = [
            c for c in mock_notifier.notify.await_args_list if c.args[0].startswith(expected_title)
        ]
        assert len(alert_calls) == 1
        assert body_hint in alert_calls[0].args[1]
        assert alert_calls[0].kwargs["hints"] is _HINT_SECURITY_ALERT
        assert alert_calls[0].kwargs["timeout_ms"] == -1
        # Tracked so a later ShieldUp can close it.
        assert subscriber._shield_down_notifs[CONTAINER] == 42

    @pytest.mark.asyncio
    async def test_shield_up_closes_tracked_down_popup(
        self, subscriber: EventSubscriber, mock_notifier: AsyncMock
    ) -> None:
        subscriber._shield_down_notifs[CONTAINER] = 77
        await subscriber._on_event(ClearanceEvent(type="shield_up", container=CONTAINER))
        for _ in range(3):
            await asyncio.sleep(0)
        mock_notifier.close.assert_awaited_once_with(77)
        assert CONTAINER not in subscriber._shield_down_notifs
        # Followed by a brief confirmation.
        confirmation = [
            c for c in mock_notifier.notify.await_args_list if c.args[0].startswith("Shield up:")
        ]
        assert len(confirmation) == 1
        assert confirmation[0].kwargs["hints"] is _HINT_CONFIRMATION

    @pytest.mark.asyncio
    async def test_shield_down_purges_pending_blocks(
        self, subscriber: EventSubscriber, mock_notifier: AsyncMock
    ) -> None:
        """Bypass means in-flight prompts are stale; drop them."""
        await subscriber._on_event(_blocked())
        assert f"{CONTAINER}:1" in subscriber._pending
        await subscriber._on_event(ClearanceEvent(type="shield_down", container=CONTAINER))
        for _ in range(3):
            await asyncio.sleep(0)
        assert f"{CONTAINER}:1" not in subscriber._pending


# ── container lifecycle ───────────────────────────────────────────────


class TestContainerLifecycle:
    """ContainerStarted/Exited fire low-urgency transient popups."""

    @pytest.mark.asyncio
    async def test_container_started_renders_lifecycle_popup(
        self, subscriber: EventSubscriber, mock_notifier: AsyncMock
    ) -> None:
        await subscriber._on_event(ClearanceEvent(type="container_started", container=CONTAINER))
        for _ in range(3):
            await asyncio.sleep(0)
        started = [
            c
            for c in mock_notifier.notify.await_args_list
            if c.args[0].startswith("Container started:")
        ]
        assert len(started) == 1
        assert started[0].kwargs["hints"] is _HINT_LIFECYCLE
        assert started[0].kwargs["timeout_ms"] == -1

    @pytest.mark.asyncio
    async def test_container_exited_renders_with_reason(
        self, subscriber: EventSubscriber, mock_notifier: AsyncMock
    ) -> None:
        await subscriber._on_event(
            ClearanceEvent(type="container_exited", container=CONTAINER, reason="poststop")
        )
        for _ in range(3):
            await asyncio.sleep(0)
        stopped = [
            c
            for c in mock_notifier.notify.await_args_list
            if c.args[0].startswith("Container stopped:")
        ]
        assert len(stopped) == 1
        assert "poststop" in stopped[0].args[1]

    @pytest.mark.asyncio
    async def test_container_exited_closes_tracked_shield_down_popup(
        self, subscriber: EventSubscriber, mock_notifier: AsyncMock
    ) -> None:
        """A dying container drops its ShieldDown popup too — no ghost alerts."""
        subscriber._shield_down_notifs[CONTAINER] = 77
        await subscriber._on_event(
            ClearanceEvent(type="container_exited", container=CONTAINER, reason="poststop")
        )
        for _ in range(3):
            await asyncio.sleep(0)
        close_ids = {c.args[0] for c in mock_notifier.close.await_args_list}
        assert 77 in close_ids
        assert CONTAINER not in subscriber._shield_down_notifs


# ── verdict routing ───────────────────────────────────────────────────


class TestVerdictRouting:
    """Action callback → ClearanceClient.verdict() dispatch."""

    @pytest.mark.asyncio
    async def test_action_callback_sends_verdict_via_client(
        self, subscriber: EventSubscriber, mock_notifier: AsyncMock
    ) -> None:
        """Clicking Allow on a notification routes through the transport."""
        await subscriber._on_event(_blocked())
        # on_action registers a callback; pick it off the mock.
        action_cb = mock_notifier.on_action.await_args.args[1]
        action_cb("allow")
        # Let the dispatched verdict coroutine run.
        for _ in range(3):
            await asyncio.sleep(0)
        subscriber._client.verdict.assert_awaited_once_with(
            CONTAINER, f"{CONTAINER}:1", DOMAIN, "allow"
        )


# ── dossier rendering ────────────────────────────────────────────────


class TestDossierRendering:
    """Per-event dossier shapes the notification body without any resolver."""

    @pytest.mark.asyncio
    async def test_task_dossier_surfaces_in_body(
        self, subscriber: EventSubscriber, mock_notifier: AsyncMock
    ) -> None:
        """A ``project + task + name`` dossier renders the task-aware body line."""
        await subscriber._on_event(
            _blocked(dossier={"project": "warp-core", "task": "t42", "name": "build"})
        )
        call = mock_notifier.notify.await_args
        assert "warp-core/t42" in call.args[1]
        assert "build" in call.args[1]
        # The notifier kwargs reflect the same identity for downstream consumers.
        assert call.kwargs["project"] == "warp-core"
        assert call.kwargs["task_id"] == "t42"
        assert call.kwargs["task_name"] == "build"

    @pytest.mark.asyncio
    async def test_empty_dossier_falls_back_to_container_id(
        self, subscriber: EventSubscriber, mock_notifier: AsyncMock
    ) -> None:
        """No orchestrator dossier → body labels the bare container ID."""
        await subscriber._on_event(_blocked(dossier={}))
        call = mock_notifier.notify.await_args
        assert CONTAINER in call.args[1]
        # Body line is "Container: <id>" (no Task: prefix).
        assert "Task:" not in call.args[1]

    @pytest.mark.asyncio
    async def test_container_name_only_dossier_renders_container_line(
        self, subscriber: EventSubscriber, mock_notifier: AsyncMock
    ) -> None:
        """A standalone container with only a ``name`` gets the Container: line."""
        await subscriber._on_event(_blocked(dossier={"name": "alpine-7"}))
        call = mock_notifier.notify.await_args
        assert "Container: alpine-7" in call.args[1]

    @pytest.mark.asyncio
    async def test_subscriber_trusts_pre_sanitised_dossier(
        self, subscriber: EventSubscriber, mock_notifier: AsyncMock
    ) -> None:
        """Subscriber forwards dossier verbatim — sanitisation happens upstream.

        The wire-format invariant is enforced once at
        ``hub.server._translate_reader_event``; subscribers see only
        printable-ASCII dossier values by the time events fan out.  See
        ``test_hub.py`` for the boundary tests that exercise the
        producer-side transformation directly.
        """
        await subscriber._on_event(
            _blocked(dossier={"name": "alpine-7", "project": "p", "task": "t"})
        )
        call = mock_notifier.notify.await_args
        assert "alpine-7" in call.args[1]
        assert call.kwargs["task_name"] == "alpine-7"

    @pytest.mark.asyncio
    async def test_verdict_uses_dossier_captured_at_block_time(
        self, subscriber: EventSubscriber, mock_notifier: AsyncMock
    ) -> None:
        """Verdict popup reuses the block's dossier even if a later event differs."""
        await subscriber._on_event(
            _blocked(dossier={"project": "p", "task": "t", "name": "original"})
        )
        mock_notifier.notify.reset_mock()
        await subscriber._on_event(
            ClearanceEvent(
                type="verdict_applied",
                container=CONTAINER,
                request_id=f"{CONTAINER}:1",
                action="allow",
                ok=True,
                dossier={"project": "p", "task": "t", "name": "renamed"},  # ignored
            )
        )
        call = mock_notifier.notify.await_args
        assert "original" in call.args[1]
        assert "renamed" not in call.args[1]


# ── category gating ──────────────────────────────────────────────────


@pytest.fixture
def filtered_subscriber_factory(
    mock_notifier: AsyncMock,
) -> Callable[[set[str]], EventSubscriber]:
    """Build a subscriber with a custom ``enabled_categories`` allowlist."""

    def _make(categories: set[str]) -> EventSubscriber:
        client = MagicMock()
        client.start = AsyncMock()
        client.stop = AsyncMock()
        client.verdict = AsyncMock(return_value=True)
        return EventSubscriber(mock_notifier, client=client, enabled_categories=categories)

    return _make


class TestEnabledCategories:
    """``enabled_categories`` gates which events render desktop popups."""

    @pytest.mark.asyncio
    async def test_default_none_keeps_every_category(self, mock_notifier: AsyncMock) -> None:
        """``enabled_categories=None`` matches the historical render-everything default."""
        client = MagicMock()
        client.start = AsyncMock()
        client.stop = AsyncMock()
        sub = EventSubscriber(mock_notifier, client=client)
        assert sub._enabled_categories == ALL_NOTIFY_CATEGORIES

    @pytest.mark.asyncio
    async def test_unknown_category_is_silently_dropped(self, mock_notifier: AsyncMock) -> None:
        """Caller noise (typos in category names) shouldn't poison the set."""
        client = MagicMock()
        client.start = AsyncMock()
        sub = EventSubscriber(
            mock_notifier,
            client=client,
            enabled_categories={NOTIFY_BLOCKED, "totally-not-a-category"},
        )
        assert sub._enabled_categories == frozenset({NOTIFY_BLOCKED})

    @pytest.mark.asyncio
    async def test_empty_set_silences_every_popup(
        self,
        filtered_subscriber_factory: Callable[[set[str]], EventSubscriber],
        mock_notifier: AsyncMock,
    ) -> None:
        """An operator who passes an empty set gets a fully muted notifier."""
        sub = filtered_subscriber_factory(set())
        for event in (
            _blocked(),
            ClearanceEvent(type="container_started", container=CONTAINER),
            ClearanceEvent(type="container_exited", container=CONTAINER, reason="poststop"),
            ClearanceEvent(type="shield_up", container=CONTAINER),
            ClearanceEvent(type="shield_down", container=CONTAINER),
        ):
            await sub._on_event(event)
        for _ in range(3):
            await asyncio.sleep(0)
        mock_notifier.notify.assert_not_called()

    @pytest.mark.asyncio
    async def test_blocked_disabled_skips_prompt_and_pending_state(
        self,
        filtered_subscriber_factory: Callable[[set[str]], EventSubscriber],
        mock_notifier: AsyncMock,
    ) -> None:
        """Without the ``blocked`` category there's no popup and nothing pending."""
        sub = filtered_subscriber_factory({NOTIFY_VERDICT})
        await sub._on_event(_blocked())
        mock_notifier.notify.assert_not_called()
        assert sub._pending == {}

    @pytest.mark.asyncio
    async def test_verdict_disabled_still_pops_pending_state(
        self,
        filtered_subscriber_factory: Callable[[set[str]], EventSubscriber],
        mock_notifier: AsyncMock,
    ) -> None:
        """Silenced verdict popups must still drain the pending dict — no leak."""
        sub = filtered_subscriber_factory({NOTIFY_BLOCKED})
        await sub._on_event(_blocked())
        assert f"{CONTAINER}:1" in sub._pending
        mock_notifier.notify.reset_mock()
        await sub._on_event(
            ClearanceEvent(
                type="verdict_applied",
                container=CONTAINER,
                request_id=f"{CONTAINER}:1",
                action="allow",
                ok=True,
            )
        )
        mock_notifier.notify.assert_not_called()
        assert f"{CONTAINER}:1" not in sub._pending

    @pytest.mark.asyncio
    async def test_lifecycle_categories_silence_passive_popups_individually(
        self,
        filtered_subscriber_factory: Callable[[set[str]], EventSubscriber],
        mock_notifier: AsyncMock,
    ) -> None:
        """Each lifecycle category toggles independently of the others."""
        sub = filtered_subscriber_factory({NOTIFY_CONTAINER_STARTED})
        for event in (
            ClearanceEvent(type="container_started", container=CONTAINER),
            ClearanceEvent(type="container_exited", container=CONTAINER, reason="poststop"),
            ClearanceEvent(type="shield_up", container=CONTAINER),
            ClearanceEvent(type="shield_down", container=CONTAINER),
        ):
            await sub._on_event(event)
        for _ in range(3):
            await asyncio.sleep(0)
        titles = [c.args[0] for c in mock_notifier.notify.await_args_list]
        assert any(t.startswith("Container started:") for t in titles)
        assert not any(t.startswith("Container stopped:") for t in titles)
        assert not any(t.startswith("Shield up:") for t in titles)
        assert not any(t.startswith("Shield down:") for t in titles)

    @pytest.mark.asyncio
    async def test_shield_up_closes_stale_down_popup_even_when_disabled(
        self,
        filtered_subscriber_factory: Callable[[set[str]], EventSubscriber],
        mock_notifier: AsyncMock,
    ) -> None:
        """Stale ShieldDown cleanup is a security concern — never gated."""
        sub = filtered_subscriber_factory({NOTIFY_SHIELD_DOWN})
        sub._shield_down_notifs[CONTAINER] = 77
        await sub._on_event(ClearanceEvent(type="shield_up", container=CONTAINER))
        for _ in range(3):
            await asyncio.sleep(0)
        mock_notifier.close.assert_awaited_once_with(77)
        assert CONTAINER not in sub._shield_down_notifs
        # No "Shield up: …" confirmation rendered.
        assert not any(
            c.args[0].startswith("Shield up:") for c in mock_notifier.notify.await_args_list
        )

    @pytest.mark.asyncio
    async def test_container_exit_purges_pending_even_when_lifecycle_disabled(
        self,
        filtered_subscriber_factory: Callable[[set[str]], EventSubscriber],
        mock_notifier: AsyncMock,
    ) -> None:
        """Pending purge runs for state hygiene regardless of popup gating."""
        sub = filtered_subscriber_factory({NOTIFY_BLOCKED})
        await sub._on_event(_blocked())
        assert f"{CONTAINER}:1" in sub._pending
        mock_notifier.notify.reset_mock()
        await sub._on_event(
            ClearanceEvent(type="container_exited", container=CONTAINER, reason="poststop")
        )
        for _ in range(3):
            await asyncio.sleep(0)
        # No "Container stopped: …" popup rendered.
        assert not any(
            c.args[0].startswith("Container stopped:") for c in mock_notifier.notify.await_args_list
        )
        assert f"{CONTAINER}:1" not in sub._pending
