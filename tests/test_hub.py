# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for [`ClearanceHub`][terok_clearance.ClearanceHub] — state machine in isolation.

Exercises the hub's internals (authz binding map, fan-out queues,
reader translation, verdict dispatch) without going through the varlink
transport.  End-to-end varlink round-trips live in ``test_client.py``.
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from terok_clearance.domain.events import ClearanceEvent
from terok_clearance.hub.server import ClearanceHub, _translate_reader_event
from terok_clearance.wire.errors import (
    InvalidAction,
    ShieldCliFailed,
    UnknownRequest,
    VerdictTupleMismatch,
)

from .conftest import CONTAINER, DEST_IP, DOMAIN

# ── Reader-event translation ──────────────────────────────────────────


class TestTranslateReaderEvent:
    """Ingester-dict → ClearanceEvent shape tests."""

    def test_connection_blocked_populates_all_fields(self) -> None:
        event = _translate_reader_event(
            "connection_blocked",
            {
                "type": "pending",
                "container": CONTAINER,
                "id": f"{CONTAINER}:1",
                "dest": DEST_IP,
                "port": 443,
                "proto": 6,
                "domain": DOMAIN,
            },
        )
        assert event.type == "connection_blocked"
        assert event.container == CONTAINER
        assert event.request_id == f"{CONTAINER}:1"
        assert event.dest == DEST_IP
        assert event.port == 443
        assert event.proto == 6
        assert event.domain == DOMAIN

    def test_connection_blocked_defaults_missing_domain(self) -> None:
        """Reader sometimes hasn't resolved a domain yet; fall through to empty."""
        event = _translate_reader_event(
            "connection_blocked",
            {
                "type": "pending",
                "container": CONTAINER,
                "id": f"{CONTAINER}:1",
                "dest": DEST_IP,
                "port": 443,
                "proto": 6,
            },
        )
        assert event.domain == ""

    def test_container_exited_carries_reason(self) -> None:
        event = _translate_reader_event(
            "container_exited",
            {"type": "container_exited", "container": CONTAINER, "reason": "poststop"},
        )
        assert event.reason == "poststop"

    def test_shield_state_event_has_only_container(self) -> None:
        event = _translate_reader_event(
            "shield_down", {"type": "shield_down", "container": CONTAINER}
        )
        assert event.type == "shield_down"
        assert event.container == CONTAINER
        assert event.request_id == ""

    def test_connection_blocked_carries_dossier(self) -> None:
        """Reader-supplied ``dossier`` survives the translation pass."""
        event = _translate_reader_event(
            "connection_blocked",
            {
                "type": "pending",
                "container": CONTAINER,
                "id": f"{CONTAINER}:1",
                "dest": DEST_IP,
                "port": 443,
                "proto": 6,
                "dossier": {"task": "abc", "project": "terok", "name": "alpine-7"},
            },
        )
        assert event.dossier == {"task": "abc", "project": "terok", "name": "alpine-7"}

    def test_missing_dossier_normalises_to_empty(self) -> None:
        """Shield-only readers (no orchestrator) ship no dossier — translator absorbs it."""
        event = _translate_reader_event(
            "connection_blocked",
            {
                "type": "pending",
                "container": CONTAINER,
                "id": f"{CONTAINER}:1",
                "dest": DEST_IP,
                "port": 443,
                "proto": 6,
            },
        )
        assert event.dossier == {}

    def test_non_object_dossier_is_dropped(self) -> None:
        """Misshaped dossier (list/scalar/null) doesn't break the translator."""
        event = _translate_reader_event(
            "connection_blocked",
            {
                "type": "pending",
                "container": CONTAINER,
                "id": f"{CONTAINER}:1",
                "dest": DEST_IP,
                "port": 443,
                "proto": 6,
                "dossier": [1, 2, 3],
            },
        )
        assert event.dossier == {}

    def test_dossier_values_are_string_coerced(self) -> None:
        """Numeric/boolean values from the reader are stringified."""
        event = _translate_reader_event(
            "shield_up",
            {"type": "shield_up", "container": CONTAINER, "dossier": {"port": 8080}},
        )
        assert event.dossier == {"port": "8080"}

    def test_attacker_controlled_domain_is_sanitised(self) -> None:
        """A crafted DNS name with control bytes loses them at the boundary."""
        event = _translate_reader_event(
            "connection_blocked",
            {
                "type": "pending",
                "container": CONTAINER,
                "id": f"{CONTAINER}:1",
                "dest": DEST_IP,
                "port": 443,
                "proto": 6,
                "domain": "evil\x1b[31mfake\x00.example.com",
            },
        )
        # No control bytes survive.
        assert "\x1b" not in event.domain
        assert "\x00" not in event.domain
        # Replacement positions stay aligned so the operator can still read it.
        assert event.domain == "evil [31mfake .example.com"

    def test_rtlo_in_domain_is_sanitised(self) -> None:
        """RTLO override (a homoglyph attack) drops to a space — non-ASCII."""
        event = _translate_reader_event(
            "connection_blocked",
            {
                "type": "pending",
                "container": CONTAINER,
                "id": f"{CONTAINER}:1",
                "dest": DEST_IP,
                "port": 443,
                "proto": 6,
                "domain": "evil\u202e.com",
            },
        )
        assert "\u202e" not in event.domain
        assert event.domain == "evil .com"

    def test_dossier_values_are_sanitised(self) -> None:
        """Container-controlled dossier strings get the same printable-ASCII rule."""
        event = _translate_reader_event(
            "shield_up",
            {
                "type": "shield_up",
                "container": CONTAINER,
                "dossier": {"name": "p1\nProtocol: spoof", "task": "café"},
            },
        )
        assert "\n" not in event.dossier["name"]
        assert event.dossier["name"] == "p1 Protocol: spoof"
        # Non-ASCII letters become spaces under the strict rule.
        assert event.dossier["task"] == "caf "

    def test_pango_markup_chars_pass_through(self) -> None:
        """``& < >`` are printable ASCII — wire layer leaves them; renderer escapes."""
        event = _translate_reader_event(
            "connection_blocked",
            {
                "type": "pending",
                "container": CONTAINER,
                "id": f"{CONTAINER}:1",
                "dest": DEST_IP,
                "port": 443,
                "proto": 6,
                "domain": "<script>alert(1)</script>",
            },
        )
        assert event.domain == "<script>alert(1)</script>"

    def test_attacker_controlled_container_id_is_sanitised(self) -> None:
        """``container`` field is also at the boundary — same rule applies."""
        event = _translate_reader_event(
            "shield_up",
            {"type": "shield_up", "container": "tainted\x00name"},
        )
        assert event.container == "tainted name"


# ── Live-verdict authz binding ────────────────────────────────────────


def _hub(**kwargs) -> ClearanceHub:
    """Build an unstarted hub — state maps are fine to test without sockets."""
    return ClearanceHub(**kwargs)


def _blocked(request_id: str = f"{CONTAINER}:1", *, domain: str = DOMAIN) -> ClearanceEvent:
    return ClearanceEvent(
        type="connection_blocked",
        container=CONTAINER,
        request_id=request_id,
        dest=DEST_IP,
        port=443,
        proto=6,
        domain=domain,
    )


class TestUpdateLiveVerdicts:
    """Authz-binding map grows and shrinks in lockstep with events."""

    def test_connection_blocked_records_domain_as_target(self) -> None:
        """Domain beats raw IP — shield dispatches ``allow_domain`` on shape."""
        hub = _hub()
        hub._update_live_verdicts(_blocked())
        assert hub._live_verdicts[f"{CONTAINER}:1"] == (CONTAINER, DOMAIN)

    def test_connection_blocked_falls_back_to_dest_when_no_domain(self) -> None:
        """Readers without dnsmasq resolution pass an empty domain."""
        hub = _hub()
        hub._update_live_verdicts(_blocked(domain=""))
        assert hub._live_verdicts[f"{CONTAINER}:1"] == (CONTAINER, DEST_IP)

    def test_shield_down_purges_bindings_for_container(self) -> None:
        """Bypass means pending blocks are stale; drop them."""
        hub = _hub()
        hub._update_live_verdicts(_blocked(f"{CONTAINER}:1"))
        hub._update_live_verdicts(_blocked(f"{CONTAINER}:2"))
        hub._update_live_verdicts(ClearanceEvent(type="shield_down", container=CONTAINER))
        assert hub._live_verdicts == {}

    def test_container_exited_purges_bindings(self) -> None:
        hub = _hub()
        hub._update_live_verdicts(_blocked())
        hub._update_live_verdicts(ClearanceEvent(type="container_exited", container=CONTAINER))
        assert hub._live_verdicts == {}

    def test_unrelated_container_not_purged(self) -> None:
        """ShieldDown for container A mustn't drop bindings for container B."""
        hub = _hub()
        hub._update_live_verdicts(_blocked(f"{CONTAINER}:1"))
        hub._update_live_verdicts(
            ClearanceEvent(
                type="connection_blocked",
                container="other",
                request_id="other:9",
                dest=DEST_IP,
                port=80,
                proto=6,
                domain="",
            )
        )
        hub._update_live_verdicts(ClearanceEvent(type="shield_down", container="other"))
        assert f"{CONTAINER}:1" in hub._live_verdicts
        assert "other:9" not in hub._live_verdicts


# ── Fan-out ───────────────────────────────────────────────────────────


class TestFanOut:
    """``_fan_out`` copies one event into every subscriber queue."""

    def test_single_subscriber_receives_event(self) -> None:
        hub = _hub()
        q: asyncio.Queue = asyncio.Queue(maxsize=4)
        hub._subscribers.add(q)
        event = _blocked()
        hub._fan_out(event)
        assert q.get_nowait() is event

    def test_full_queue_drops_oldest(self) -> None:
        """Slow subscribers lose the oldest event rather than blocking fan-out."""
        hub = _hub()
        q: asyncio.Queue = asyncio.Queue(maxsize=2)
        hub._subscribers.add(q)
        for i in range(4):
            hub._fan_out(_blocked(f"{CONTAINER}:{i}"))
        assert q.qsize() == 2
        latest = q.get_nowait()
        second = q.get_nowait()
        assert latest.request_id == f"{CONTAINER}:2"
        assert second.request_id == f"{CONTAINER}:3"

    def test_fan_out_touches_every_subscriber(self) -> None:
        hub = _hub()
        queues = [asyncio.Queue(maxsize=4) for _ in range(3)]
        for q in queues:
            hub._subscribers.add(q)
        hub._fan_out(_blocked())
        for q in queues:
            assert q.qsize() == 1


# ── Verdict dispatch ──────────────────────────────────────────────────


async def _stub_shield_ok(*args, **kwargs) -> tuple[bool, str]:  # noqa: ANN002,ANN003
    return True, ""


async def _stub_shield_fail(*args, **kwargs) -> tuple[bool, str]:  # noqa: ANN002,ANN003
    return False, "nft lock"


class TestApplyVerdict:
    """``_apply_verdict`` enforces the four refusal paths + fans out success."""

    @pytest.mark.asyncio
    async def test_refuses_unknown_action(self) -> None:
        hub = _hub()
        with pytest.raises(InvalidAction) as exc_info:
            await hub._apply_verdict(CONTAINER, f"{CONTAINER}:1", DOMAIN, "maybe")
        assert exc_info.value.action == "maybe"

    @pytest.mark.asyncio
    async def test_refuses_unknown_request_id(self) -> None:
        hub = _hub()
        with pytest.raises(UnknownRequest) as exc_info:
            await hub._apply_verdict(CONTAINER, "ghost:42", DOMAIN, "allow")
        assert exc_info.value.request_id == "ghost:42"

    @pytest.mark.asyncio
    async def test_refuses_tuple_mismatch(self) -> None:
        """A request_id the hub emitted but for a different (container, dest)."""
        hub = _hub()
        hub._update_live_verdicts(_blocked())
        with pytest.raises(VerdictTupleMismatch) as exc_info:
            await hub._apply_verdict("wrong-container", f"{CONTAINER}:1", DOMAIN, "allow")
        assert exc_info.value.expected_container == CONTAINER
        assert exc_info.value.got_container == "wrong-container"
        # Entry survives so a subsequent matching verdict still works.
        assert f"{CONTAINER}:1" in hub._live_verdicts

    @pytest.mark.asyncio
    async def test_success_fans_out_verdict_applied_event(self) -> None:
        hub = _hub()
        hub._update_live_verdicts(_blocked())
        hub._verdict_client.apply = _stub_shield_ok
        q: asyncio.Queue = asyncio.Queue(maxsize=4)
        hub._subscribers.add(q)

        ok = await hub._apply_verdict(CONTAINER, f"{CONTAINER}:1", DOMAIN, "allow")

        assert ok is True
        # Live binding released on success.
        assert hub._live_verdicts == {}
        # Every subscriber sees a verdict_applied event.
        event = q.get_nowait()
        assert event.type == "verdict_applied"
        assert event.ok is True
        assert event.action == "allow"

    @pytest.mark.asyncio
    async def test_shield_failure_raises_and_still_fans_out(self) -> None:
        """Shield failure flows BOTH as a raised error AND as ok=false event."""
        hub = _hub()
        hub._update_live_verdicts(_blocked())
        hub._verdict_client.apply = _stub_shield_fail
        q: asyncio.Queue = asyncio.Queue(maxsize=4)
        hub._subscribers.add(q)

        with pytest.raises(ShieldCliFailed) as exc_info:
            await hub._apply_verdict(CONTAINER, f"{CONTAINER}:1", DOMAIN, "allow")

        assert exc_info.value.stderr == "nft lock"
        event = q.get_nowait()
        assert event.type == "verdict_applied"
        assert event.ok is False

    @pytest.mark.asyncio
    async def test_shield_failure_restores_authz_binding(self) -> None:
        """A transient shield failure must leave the binding intact for retries."""
        hub = _hub()
        hub._update_live_verdicts(_blocked())
        hub._verdict_client.apply = _stub_shield_fail
        request_id = f"{CONTAINER}:1"

        with pytest.raises(ShieldCliFailed):
            await hub._apply_verdict(CONTAINER, request_id, DOMAIN, "allow")

        assert hub._live_verdicts[request_id] == (CONTAINER, DOMAIN)


# ── Reader-event relay (end-to-end internals) ─────────────────────────


class TestRelayReaderEvent:
    """Ingester-dict → typed event → live_verdicts + subscriber fan-out."""

    @pytest.mark.asyncio
    async def test_full_pending_event_lands_on_subscriber(self) -> None:
        hub = _hub()
        q: asyncio.Queue = asyncio.Queue(maxsize=4)
        hub._subscribers.add(q)
        await hub._relay_reader_event(
            {
                "type": "pending",
                "container": CONTAINER,
                "id": f"{CONTAINER}:1",
                "dest": DEST_IP,
                "port": 443,
                "proto": 6,
                "domain": DOMAIN,
            }
        )
        event = q.get_nowait()
        assert event.type == "connection_blocked"
        assert event.request_id == f"{CONTAINER}:1"
        # Authz binding recorded for the follow-up Verdict.
        assert f"{CONTAINER}:1" in hub._live_verdicts

    @pytest.mark.asyncio
    async def test_unknown_type_is_silently_dropped(self) -> None:
        hub = _hub()
        q: asyncio.Queue = asyncio.Queue(maxsize=4)
        hub._subscribers.add(q)
        await hub._relay_reader_event({"type": "unheard-of", "container": CONTAINER})
        assert q.empty()

    @pytest.mark.asyncio
    async def test_malformed_event_is_swallowed(self) -> None:
        """A missing required field (e.g. container) doesn't kill the ingester."""
        hub = _hub()
        q: asyncio.Queue = asyncio.Queue(maxsize=4)
        hub._subscribers.add(q)
        await hub._relay_reader_event({"type": "pending"})  # no container, no id
        assert q.empty()


# ── start() rollback ──────────────────────────────────────────────────


class TestStartRollback:
    """``start()`` must not leak a live ingester when the varlink bind fails."""

    @pytest.mark.asyncio
    async def test_bind_failure_stops_ingester(self) -> None:
        """If bind_hardened raises, the already-started ingester is stopped + cleared."""
        hub = _hub()
        ingester = AsyncMock()
        with (
            patch("terok_clearance.hub.server.EventIngester", return_value=ingester),
            patch(
                "terok_clearance.wire.socket.bind_hardened",
                side_effect=OSError("simulated bind failure"),
            ),
        ):
            with pytest.raises(OSError, match="simulated bind failure"):
                await hub.start()
        ingester.start.assert_awaited_once()
        ingester.stop.assert_awaited_once()
        assert hub._ingester is None
        assert hub._varlink_server is None


# ── Verdict TTL / expiry ──────────────────────────────────────────────


class TestParseVerdictTtl:
    """TTL string parsing."""

    def test_none_uses_default(self) -> None:
        """Unset env / None falls back to the 24h default."""
        from terok_clearance.hub.server import _DEFAULT_VERDICT_TTL, parse_verdict_ttl

        assert parse_verdict_ttl(None) == _DEFAULT_VERDICT_TTL

    def test_plain_seconds(self) -> None:
        from terok_clearance.hub.server import parse_verdict_ttl

        assert parse_verdict_ttl("3600") == 3600.0

    def test_unit_suffixes(self) -> None:
        from terok_clearance.hub.server import parse_verdict_ttl

        assert parse_verdict_ttl("30m") == 1800.0
        assert parse_verdict_ttl("24h") == 86400.0
        assert parse_verdict_ttl("7d") == 604800.0
        assert parse_verdict_ttl("60s") == 60.0

    def test_fractional(self) -> None:
        from terok_clearance.hub.server import parse_verdict_ttl

        assert parse_verdict_ttl("1.5h") == 5400.0

    def test_never_disables_expiry(self) -> None:
        from terok_clearance.hub.server import parse_verdict_ttl

        for v in ("never", "none", "0", "off", "disabled"):
            assert parse_verdict_ttl(v) is None, f"{v!r} should disable expiry"

    def test_invalid_raises(self) -> None:
        from terok_clearance.hub.server import parse_verdict_ttl

        with pytest.raises(ValueError):
            parse_verdict_ttl("abc")


class TestVerdictTtlExpiry:
    """Background cleanup of stale pending-verdict entries."""

    def test_ttl_disabled_via_config_never(self, monkeypatch) -> None:
        """clearance.verdict_ttl: never in config.yml disables expiry."""
        monkeypatch.setattr(
            "terok_clearance.hub.server._read_verdict_ttl_from_config",
            lambda: None,
        )
        hub = _hub()
        assert hub.verdict_ttl is None

    def test_ttl_from_config(self, monkeypatch) -> None:
        """TTL is read from the clearance.verdict_ttl config key."""
        monkeypatch.setattr(
            "terok_clearance.hub.server._read_verdict_ttl_from_config",
            lambda: 3600.0,
        )
        hub = _hub()
        assert hub.verdict_ttl == 3600.0

    def test_explicit_ttl_overrides_config(self, monkeypatch) -> None:
        """Constructor parameter takes precedence over config file."""
        monkeypatch.setattr(
            "terok_clearance.hub.server._read_verdict_ttl_from_config",
            lambda: 3600.0,
        )
        from terok_clearance.hub.server import ClearanceHub

        hub = ClearanceHub(verdict_ttl=60.0)
        assert hub.verdict_ttl == 60.0

    def test_config_read_failure_falls_back_to_default(self, monkeypatch) -> None:
        """Config read failure falls back to the 24h default."""
        from terok_clearance.hub.server import _DEFAULT_VERDICT_TTL, ClearanceHub

        monkeypatch.setattr(
            "terok_clearance.hub.server._read_verdict_ttl_from_config",
            lambda: _DEFAULT_VERDICT_TTL,
        )
        hub = ClearanceHub()
        assert hub.verdict_ttl == _DEFAULT_VERDICT_TTL

    def test_cleanup_removes_stale_entries(self) -> None:
        """Entries older than the TTL are removed by cleanup."""
        import time

        hub = _hub(verdict_ttl=1.0)  # 1 second TTL
        hub._update_live_verdicts(_blocked(f"{CONTAINER}:1"))
        hub._update_live_verdicts(_blocked(f"{CONTAINER}:2"))
        assert len(hub._live_verdicts) == 2

        # Backdate the first entry
        hub._live_verdicts_ts[f"{CONTAINER}:1"] = time.monotonic() - 2.0

        removed = hub._cleanup_stale_verdicts()
        assert removed == 1
        assert f"{CONTAINER}:1" not in hub._live_verdicts
        assert f"{CONTAINER}:2" in hub._live_verdicts

    def test_cleanup_keeps_fresh_entries(self) -> None:
        """Entries newer than the TTL are kept."""
        hub = _hub(verdict_ttl=3600.0)  # 1 hour TTL
        hub._update_live_verdicts(_blocked())
        removed = hub._cleanup_stale_verdicts()
        assert removed == 0
        assert len(hub._live_verdicts) == 1

    def test_cleanup_noop_when_ttl_disabled(self) -> None:
        """Cleanup is a no-op when expiry is disabled."""
        import time

        hub = _hub(verdict_ttl=None)
        hub._update_live_verdicts(_blocked())
        # Backdate the entry
        hub._live_verdicts_ts[f"{CONTAINER}:1"] = time.monotonic() - 999999.0
        removed = hub._cleanup_stale_verdicts()
        assert removed == 0
        assert len(hub._live_verdicts) == 1

    def test_timestamps_removed_on_lifecycle_purge(self) -> None:
        """Lifecycle events remove both the binding and its timestamp."""
        hub = _hub(verdict_ttl=3600.0)
        hub._update_live_verdicts(_blocked(f"{CONTAINER}:1"))
        assert f"{CONTAINER}:1" in hub._live_verdicts_ts
        hub._update_live_verdicts(ClearanceEvent(type="shield_down", container=CONTAINER))
        assert f"{CONTAINER}:1" not in hub._live_verdicts
        assert f"{CONTAINER}:1" not in hub._live_verdicts_ts

    def test_timestamps_removed_on_verdict(self) -> None:
        """A successful verdict removes both the binding and its timestamp."""
        hub = _hub(verdict_ttl=3600.0)
        hub._verdict_client = AsyncMock()
        hub._verdict_client.apply = AsyncMock(return_value=(True, ""))
        hub._update_live_verdicts(_blocked())
        rid = f"{CONTAINER}:1"
        assert rid in hub._live_verdicts_ts

        asyncio.run(hub._apply_verdict(CONTAINER, rid, DOMAIN, "allow"))
        assert rid not in hub._live_verdicts
        assert rid not in hub._live_verdicts_ts

    def test_timestamp_preserved_on_verdict_mismatch(self) -> None:
        """A mismatched verdict puts the entry back with its timestamp."""
        hub = _hub(verdict_ttl=3600.0)
        hub._update_live_verdicts(_blocked())
        rid = f"{CONTAINER}:1"
        original_ts = hub._live_verdicts_ts[rid]

        with pytest.raises(VerdictTupleMismatch):
            asyncio.run(hub._apply_verdict(CONTAINER, rid, "wrong-dest", "allow"))
        assert rid in hub._live_verdicts
        assert hub._live_verdicts_ts[rid] == original_ts


class TestReadVerdictTtlFromConfig:
    """Config-file reading for the pending-verdict TTL."""

    def test_absent_key_returns_default(self, monkeypatch) -> None:
        """No clearance.verdict_ttl key → 24h default."""
        from terok_clearance.hub.server import _DEFAULT_VERDICT_TTL, _read_verdict_ttl_from_config

        monkeypatch.setattr(
            "terok_util.paths.read_config_section",
            lambda section: {},
        )
        assert _read_verdict_ttl_from_config() == _DEFAULT_VERDICT_TTL

    def test_parses_duration_string(self, monkeypatch) -> None:
        """Config value '1h' → 3600 seconds."""
        from terok_clearance.hub.server import _read_verdict_ttl_from_config

        monkeypatch.setattr(
            "terok_util.paths.read_config_section",
            lambda section: {"verdict_ttl": "1h"} if section == "clearance" else {},
        )
        assert _read_verdict_ttl_from_config() == 3600.0

    def test_never_disables_expiry(self, monkeypatch) -> None:
        """Config value 'never' → None (no expiry)."""
        from terok_clearance.hub.server import _read_verdict_ttl_from_config

        monkeypatch.setattr(
            "terok_util.paths.read_config_section",
            lambda section: {"verdict_ttl": "never"} if section == "clearance" else {},
        )
        assert _read_verdict_ttl_from_config() is None

    def test_invalid_value_falls_back_to_default(self, monkeypatch) -> None:
        """Invalid config value → 24h default (fail-silent)."""
        from terok_clearance.hub.server import _DEFAULT_VERDICT_TTL, _read_verdict_ttl_from_config

        monkeypatch.setattr(
            "terok_util.paths.read_config_section",
            lambda section: {"verdict_ttl": "garbage"} if section == "clearance" else {},
        )
        assert _read_verdict_ttl_from_config() == _DEFAULT_VERDICT_TTL

    def test_read_failure_falls_back_to_default(self, monkeypatch) -> None:
        """Config read exception → 24h default (fail-silent)."""
        from terok_clearance.hub.server import _DEFAULT_VERDICT_TTL, _read_verdict_ttl_from_config

        def _boom(section: str) -> dict:
            raise RuntimeError("config unreadable")

        monkeypatch.setattr("terok_util.paths.read_config_section", _boom)
        assert _read_verdict_ttl_from_config() == _DEFAULT_VERDICT_TTL

    def test_timestamp_preserved_on_shield_failure(self) -> None:
        """A failed shield call restores the binding with its original timestamp."""
        hub = _hub(verdict_ttl=3600.0)
        hub._verdict_client = AsyncMock()
        hub._verdict_client.apply = AsyncMock(return_value=(False, "shield failed"))
        hub._update_live_verdicts(_blocked())
        rid = f"{CONTAINER}:1"
        original_ts = hub._live_verdicts_ts[rid]

        with pytest.raises(ShieldCliFailed):
            asyncio.run(hub._apply_verdict(CONTAINER, rid, DOMAIN, "allow"))

        # Binding and timestamp are restored for retry
        assert rid in hub._live_verdicts
        assert hub._live_verdicts_ts[rid] == original_ts
