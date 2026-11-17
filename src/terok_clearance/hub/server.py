# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""The clearance hub — varlink server + reader ingester + verdict exec.

Fans reader-emitted events (blocks, container lifecycle, shield state)
out to every connected clearance client, and applies verdicts the
clients send back by shelling out to ``terok-shield allow|deny``.  The
only D-Bus in sight is what individual clients choose to use on their
own (the desktop notifier reaches for ``org.freedesktop.Notifications``
out-of-band); the hub itself speaks plain unix-socket varlink.

Authorisation is structural: the socket is mode 0600 (same-UID only),
and every ``Verdict`` call must cite a ``(container, request_id, dest)``
triple the hub actually emitted via ``connection_blocked``.  The
triple is recorded at emit time and dropped on verdict or lifecycle
change; anything that doesn't match is a [`UnknownRequest`][terok_clearance.hub.server.UnknownRequest] or
[`VerdictTupleMismatch`][terok_clearance.hub.server.VerdictTupleMismatch] refusal.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import re
import time
from collections.abc import AsyncIterator, Callable, Iterable
from contextlib import AbstractContextManager
from pathlib import Path

from asyncvarlink import VarlinkInterfaceRegistry, VarlinkUnixServer, create_unix_server
from asyncvarlink.serviceinterface import VarlinkServiceInterface

from terok_clearance.domain.events import VERDICT_ACTIONS, ClearanceEvent, Dossier
from terok_clearance.domain.verdict_ttl import DEFAULT_VERDICT_TTL, parse_verdict_ttl
from terok_clearance.hub.ingester import EventIngester
from terok_clearance.verdict.client import VerdictClient
from terok_clearance.wire.errors import (
    InvalidAction,
    ShieldCliFailed,
    UnknownContainer,
    UnknownRequest,
    VerdictTupleMismatch,
)
from terok_clearance.wire.interface import Clearance1Interface
from terok_clearance.wire.sanitize import sanitize, sanitize_mapping
from terok_clearance.wire.socket import default_clearance_socket_path

_log = logging.getLogger(__name__)

#: Depth of per-subscriber event queues.  Slow subscribers don't block
#: fan-out to other clients — the hub drops their oldest events once
#: this limit is reached.  Desktop popups + TUI rows are an instant-ish
#: render surface, so a modest depth is plenty; keeping the queue
#: bounded also prevents a stuck client from pinning arbitrary memory.
_SUBSCRIBER_QUEUE_DEPTH = 128

#: Config-file key (under the ``clearance:`` section of
#: ``~/.config/terok/config.yml``) holding the pending-verdict TTL.
#: Human-readable duration (``24h``, ``30m``, ``7d``) or ``never``.
VERDICT_TTL_CONFIG_KEY = "verdict_ttl"

#: How often the background cleanup task scans for stale entries.
_VERDICT_CLEANUP_INTERVAL = 300.0  # 5 minutes

#: The only key a per-container hub accepts for its container: the 12-char
#: short id the shield reader stamps on every event.  The reader takes the
#: first 12 characters of the podman id and accepts only hex, so this is the
#: same grammar.  A podman name or the full 64-char id would re-key every
#: event to a value no short-id caller uses, so a mute would silently
#: do nothing.
_SHORT_ID_RE = re.compile(r"[0-9a-fA-F]{12}")

#: Length cap for the shield output carried as a failed verdict's ``reason``.
#: It is the same 512 the verdict helper already caps that output at.
#: Shield puts the next step at the end of its refusal line ("use a
#: shield.override ..."), and the default wire cap of 256 would cut that
#: end off a long host name.
_VERDICT_REASON_MAX_LEN = 512


def _read_verdict_ttl_from_config() -> float | None:
    """Read the pending-verdict TTL from the terok config file.

    Reads ``clearance.verdict_ttl`` from the layered terok config
    (``/etc/terok/config.yml`` → ``~/.config/terok/config.yml``) via
    terok-util's [`read_config_section`][terok_util.paths.read_config_section].
    Returns the 24h default when the key is absent; ``None`` (no expiry)
    when set to ``never``.

    Fail-silent: any error reading or parsing the config falls back to
    the default rather than crashing the hub.
    """
    try:
        from terok_util.paths import read_config_section

        raw = read_config_section("clearance").get(VERDICT_TTL_CONFIG_KEY)
        if raw is None:
            return DEFAULT_VERDICT_TTL
        return parse_verdict_ttl(raw)
    except Exception:  # noqa: BLE001 - fail-silent; bad config must not crash the hub
        _log.warning(
            "failed to read clearance.verdict_ttl from config — using default", exc_info=True
        )
        return DEFAULT_VERDICT_TTL


#: Reader ``type`` → wire-level ``ClearanceEvent.type``.  Only one event
#: renames (``pending → connection_blocked``); every other reader type
#: flows through unchanged.  Kept as an explicit allowlist so unknown
#: values get dropped at `ClearanceHub._relay_reader_event` rather
#: than leaking to clients.
_WIRE_EVENT_TYPES: frozenset[str] = frozenset(
    {
        "container_started",
        "container_exited",
        "shield_up",
        "shield_down",
        "shield_disengaged",
    }
)


#: Sentinel type for ``ClearanceHub.__init__``'s ``verdict_ttl`` parameter —
#: distinguishes "not passed" (read config/default) from "explicitly None"
#: (disable expiry).  Using a dedicated class (rather than ``object()``)
#: keeps mypy happy: the parameter type is a union that includes the
#: sentinel, and the narrowing check ``is _UNSET`` is well-typed.
class _Unset:
    """Sentinel for "parameter not passed" — distinct from ``None``."""

    __slots__ = ()

    def __repr__(self) -> str:
        return "<unset>"


_UNSET = _Unset()

#: Type alias for the ``verdict_ttl`` parameter: a TTL in seconds, ``None``
#: to disable expiry, or the ``_UNSET`` sentinel to read from config.
VerdictTtlParam = float | None | _Unset


class ClearanceHub:
    """Server for the ``org.terok.Clearance1`` interface.

    Owns four pieces of state:

    * ``_subscribers`` — a set of bounded per-connection queues; the hub
      puts a [`ClearanceEvent`][terok_clearance.ClearanceEvent] on each one every time the reader
      ingester delivers an event.  Slow clients see their oldest events
      dropped; fast clients aren't affected.
    * ``_live_verdicts`` — the ``request_id → (container, dest)`` map
      the ``Verdict`` method checks for the authz binding.
    * ``_muted`` — the containers whose prompts are dropped, seeded from
      the launch default and changed by ``SetMute``.
    * An [`EventIngester`][terok_clearance.hub.ingester.EventIngester] bound to the canonical reader socket.

    Wherever the hub keys a container — bindings, mutes, the events it
    fans out — it uses the key the events carry: the 12-char short id the
    shield reader stamps on what it emits.  A hub built for one container
    makes that the only key it holds (see the *container* argument), so a
    name can never be mistaken for it.

    Lifecycle: [`start`][terok_clearance.hub.server.ClearanceHub.start] brings everything up; [`stop`][terok_clearance.hub.server.ClearanceHub.stop] tears
    it down under individual timeouts so a flaky bus or a stuck
    subscriber can't stall the per-container supervisor's teardown.
    """

    def __init__(
        self,
        *,
        clearance_socket: Path | None = None,
        reader_socket: Path | None = None,
        verdict_client: VerdictClient | None = None,
        socket_context: Callable[[], AbstractContextManager[None]] | None = None,
        verdict_ttl: VerdictTtlParam = _UNSET,
        container: str | None = None,
        aliases: Iterable[str] = (),
        muted: Iterable[str] = (),
    ) -> None:
        """Configure the two sockets, the verdict-helper client and the mute state.

        ``verdict_client`` is injected so tests can stub out shield exec
        without spawning the helper process.  Production callers leave
        it defaulted — a fresh [`VerdictClient`][terok_clearance.hub.server.VerdictClient] pointing at the
        canonical helper socket.

        *socket_context* — optional zero-arg callable returning a context
        manager around the varlink ``bind()`` (forwarded to
        [`bind_hardened`][terok_clearance.wire.socket.bind_hardened]).
        terok-sandbox passes its SELinux ``setsockcreatecon`` helper here
        so the hub socket is labelled ``terok_socket_t`` and confined
        containers can ``connectto`` it.

        *verdict_ttl* — seconds after which a pending ``connection_blocked``
        entry is expired from the authz-binding map.  ``None`` means no
        expiry (backward-compatible).  When not passed explicitly, the
        value is read from the ``clearance.verdict_ttl`` key in the
        layered terok config (``~/.config/terok/config.yml``); absent
        falls back to a 24-hour default so long-running containers
        don't accumulate unbounded state.  Pass ``verdict_ttl=None``
        explicitly to disable expiry regardless of the config file.

        Config file example::

            clearance:
              verdict_ttl: 24h   # or "never" to disable

        *container* — the one container this hub serves, under the key the
        shield reader stamps on its events: the 12-char short id.  Pass it
        only when *reader_socket* is per-container, as in the supervisor's
        ``events/<short_id>/ingester.sock`` layout: every event arriving
        there is about this container by construction, so the hub stamps
        each one with this key.  That matters because producers disagree —
        the reader sends the short id while host-side ``shield_up`` /
        ``shield_down`` events arrive under the podman name — and a split
        identity lets a shield drop miss the very prompts it should retire.
        It also confines every verdict this hub can issue to its own
        container.  ``None`` (the default) keeps a multi-container hub that
        relays each event's key as sent.  Anything but 12 hex characters
        raises ``ValueError``.  A podman name or the full id would be
        accepted and then become the key on every event, which no
        short-id caller could address.  The launcher would see nothing
        wrong while every mute did nothing.

        *aliases* — other spellings of *container* that a ``SetMute``
        caller or the launch default may use: the full id, the podman name.
        They resolve to *container*; with *container* set, any other key is
        refused with [`UnknownContainer`][terok_clearance.wire.errors.UnknownContainer].
        Meaningless without *container*, so passing them alone raises
        ``ValueError``.

        *muted* — containers whose prompts start muted: the launch default.
        A launcher that passes it on every construction (the per-container
        supervisor builds a fresh hub on each container start) gets a
        default that holds across hub restarts, where a runtime ``SetMute``
        deliberately does not.  It is applied here, before
        [`start`][terok_clearance.hub.server.ClearanceHub.start] binds the
        ingester, so no prompt can slip out ahead of it.  A key this hub
        does not serve is logged and ignored: the container stays audible,
        the safe direction for a default that cannot be honoured.

        *aliases* and *muted* take collections of keys.  Passing a single
        string raises ``TypeError``: a string is itself iterable, so it
        would be split into one key per character and the intended key
        would never be used.
        """
        self._clearance_socket = clearance_socket or default_clearance_socket_path()
        self._reader_socket = reader_socket  # None → EventIngester picks its default.
        self._verdict_client = verdict_client or VerdictClient()
        self._socket_context = socket_context
        ttl: float | None
        if verdict_ttl is _UNSET:
            ttl = _read_verdict_ttl_from_config()
        elif verdict_ttl is None:
            ttl = None
        elif isinstance(verdict_ttl, (int, float)):
            ttl = float(verdict_ttl)
        else:  # pragma: no cover - defensive; type narrowing should prevent this
            ttl = DEFAULT_VERDICT_TTL
        self._verdict_ttl: float | None = ttl

        self._subscribers: set[asyncio.Queue[ClearanceEvent]] = set()
        # request_id → (container, dest) the hub emitted in the matching
        # ConnectionBlocked; Verdict calls must cite a triple that matches.
        self._live_verdicts: dict[str, tuple[str, str]] = {}
        # request_id → monotonic timestamp when the entry was recorded;
        # used by the background cleanup to expire stale entries.
        self._live_verdicts_ts: dict[str, float] = {}
        self._cleanup_task: asyncio.Task[None] | None = None

        for parameter, keys in (("aliases", aliases), ("muted", muted)):
            if isinstance(keys, str):
                raise TypeError(
                    f"{parameter} takes a collection of container keys, not the string {keys!r}"
                )
        if container is not None and not _SHORT_ID_RE.fullmatch(container):
            raise ValueError(
                "container must be the 12-hex-digit short id the shield reader stamps "
                f"on its events, not {container!r}"
            )
        alias_keys = tuple(alias for alias in aliases if alias)
        if container is None and alias_keys:
            raise ValueError("aliases need the container they stand for")
        self._container = container
        # Every key a mute may arrive under → the key events carry.  Empty
        # for a multi-container hub, which takes keys as given.
        self._mute_keys: dict[str, str] = (
            {}
            if container is None
            else dict.fromkeys(alias_keys, container) | {container: container}
        )
        # Containers whose refusals are not put to the operator.  The reader
        # still writes them to the audit log while the task's shield audit is
        # on; with audit off, a muted refusal leaves no record anywhere.
        # Held in memory and rebuilt from the launch default on every start,
        # so a runtime mute ends with the hub; ``Mutes()`` reads it back.
        self._muted: set[str] = set()
        for key in muted:
            self._seed_mute(key)

        self._ingester: EventIngester | None = None
        self._varlink_server: VarlinkUnixServer | None = None

    @property
    def verdict_ttl(self) -> float | None:
        """Pending-verdict TTL in seconds, or ``None`` when expiry is disabled."""
        return self._verdict_ttl

    # ── lifecycle ──────────────────────────────────────────────────────

    async def start(self) -> None:
        """Bring the ingester + varlink server online and accept clients.

        Transactional: if the varlink bind fails after the ingester is
        already listening, the ingester is stopped before the exception
        propagates so a half-started hub doesn't leak a live
        reader-side socket on systemd restart paths.
        """
        self._ingester = EventIngester(
            socket_path=self._reader_socket or _default_reader_socket(),
            on_event=self._relay_reader_event,
        )
        await self._ingester.start()
        try:
            registry = VarlinkInterfaceRegistry()
            registry.register_interface(
                Clearance1Interface(
                    event_stream_factory=self._subscribe,
                    apply_verdict=self._apply_verdict,
                    set_mute=self._set_mute,
                    mutes=self._mutes,
                )
            )
            registry.register_interface(
                VarlinkServiceInterface(
                    vendor="terok",
                    product="terok-clearance",
                    version=_own_version(),
                    url="https://github.com/terok-ai/terok-clearance",
                    registry=registry,
                )
            )

            from terok_clearance.wire.socket import bind_hardened

            async def _factory(path: str) -> object:
                return await create_unix_server(registry.protocol_factory, path=path)

            self._varlink_server = await bind_hardened(
                _factory,
                self._clearance_socket,
                "clearance",
                socket_context=self._socket_context,
            )
        except BaseException:
            with contextlib.suppress(Exception):
                await self._ingester.stop()
            self._ingester = None
            raise
        # Start the background cleanup task for stale verdict entries.
        # Only runs when a TTL is configured; otherwise entries live until
        # a verdict or lifecycle event removes them (backward-compatible).
        if self._verdict_ttl is not None:
            self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        _log.info(
            "clearance hub online at %s (verdict_ttl=%s)",
            self._clearance_socket,
            f"{self._verdict_ttl}s" if self._verdict_ttl is not None else "never",
        )

    async def stop(self) -> None:
        """Close the varlink server + ingester; drain subscriber queues."""
        if self._varlink_server is not None:
            # ``close()`` on its own only stops accepting new connections;
            # existing subscribers would sit forever in ``queue.get()`` and
            # ``wait_closed`` would hang until the timeout fires.
            # ``close_clients()`` walks the live transports and closes them,
            # which makes the server-side ``_call_async_method_more``'s
            # next ``send_reply`` fail with OSError — that in turn calls
            # ``generator.aclose()`` on the subscriber, propagating cleanly
            # through to our ``finally`` block.  This avoids the
            # assertion asyncvarlink fires when a streaming generator
            # ends "normally" with ``continues=True`` on the last reply.
            self._varlink_server.close()
            with contextlib.suppress(AttributeError):
                self._varlink_server.close_clients()
            with contextlib.suppress(TimeoutError, Exception):
                await asyncio.wait_for(self._varlink_server.wait_closed(), timeout=1.0)
            self._varlink_server = None
        if self._ingester is not None:
            with contextlib.suppress(Exception):
                await self._ingester.stop()
            self._ingester = None
        with contextlib.suppress(Exception):
            await self._verdict_client.stop()
        if self._cleanup_task is not None:
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._cleanup_task
            self._cleanup_task = None
        self._subscribers.clear()
        self._live_verdicts.clear()
        self._live_verdicts_ts.clear()

    # ── reader ingestion ───────────────────────────────────────────────

    async def _relay_reader_event(self, raw: dict) -> None:  # NOSONAR S7503
        """Translate one ingester dict → a [`ClearanceEvent`][terok_clearance.ClearanceEvent] + fan it out.

        Records the authz binding on ``connection_blocked`` events and
        releases it on ``verdict_applied`` / lifecycle changes, so the
        ``Verdict`` method can pass or refuse without re-consulting the
        reader.  Malformed events are logged and dropped — one bad line
        from a rogue reader mustn't kill the ingester.  A hub built for one
        container stamps each event with that container's key first, so the
        bindings, the mute set and every subscriber see a single identity.
        """
        raw_type = raw.get("type", "")
        # Only the ``pending → connection_blocked`` renaming differs;
        # every other reader type flows through with the same name.
        wire_type = "connection_blocked" if raw_type == "pending" else raw_type
        if wire_type != "connection_blocked" and wire_type not in _WIRE_EVENT_TYPES:
            _log.debug("dropping unknown reader event type %r", raw_type)
            return
        try:
            event = _translate_reader_event(wire_type, raw)
        except (KeyError, ValueError, TypeError) as exc:
            _log.warning("dropping malformed reader event %r: %s", raw, exc)
            return
        if self._container is not None and event.container != self._container:
            # A per-container ingester only ever hears about its own container,
            # whatever key the producer chose (host-side shield events use the
            # podman name); one key is what lets mutes and purges match.
            _log.debug("%s for %r stamped as %s", event.type, event.container, self._container)
            event.container = self._container
        if event.type == "connection_blocked" and event.container in self._muted:
            # The shield already refused this connection and the reader already
            # recorded it; muting drops the question, nothing else.  Dropped
            # before the verdict binding too — a request nobody is shown must
            # not sit in the map waiting for an answer that cannot come.
            _log.debug("muted: dropping prompt for %s → %s", event.container, event.dest)
            return
        self._update_live_verdicts(event)
        self._fan_out(event)

    def _update_live_verdicts(self, event: ClearanceEvent) -> None:
        """Maintain the authz-binding map in lockstep with the event stream.

        The bound ``dest`` is the "target" shield will actually operate
        on — the domain when the reader resolved one via dnsmasq (shield
        dispatches ``allow_domain`` on shape so future DNS rotations
        track), else the raw IP.  Clients send the same value back as
        ``Verdict.dest``; binding on anything else would force a
        pointless translation pass on every verdict.
        """
        if event.type == "connection_blocked" and event.request_id:
            self._live_verdicts[event.request_id] = (
                event.container,
                event.domain or event.dest,
            )
            self._live_verdicts_ts[event.request_id] = time.monotonic()
        elif event.type in {"shield_down", "shield_disengaged", "container_exited"}:
            stale = [
                rid
                for rid, (container, _) in self._live_verdicts.items()
                if container == event.container
            ]
            for rid in stale:
                self._live_verdicts.pop(rid, None)
                self._live_verdicts_ts.pop(rid, None)
            if event.type == "container_exited":
                self._muted.discard(event.container)

    def _cleanup_stale_verdicts(self) -> int:
        """Expire pending-verdict entries older than the configured TTL.

        Returns the number of entries removed.  No-op when expiry is
        disabled (``verdict_ttl is None``).
        """
        if self._verdict_ttl is None:
            return 0
        now = time.monotonic()
        stale = [rid for rid, ts in self._live_verdicts_ts.items() if now - ts > self._verdict_ttl]
        for rid in stale:
            self._live_verdicts.pop(rid, None)
            self._live_verdicts_ts.pop(rid, None)
        if stale:
            _log.info(
                "expired %d stale pending-verdict entries (ttl=%gs)",
                len(stale),
                self._verdict_ttl,
            )
        return len(stale)

    async def _cleanup_loop(self) -> None:
        """Periodically expire stale pending-verdict entries.

        Runs until cancelled (hub shutdown).  The interval is deliberately
        much shorter than any realistic TTL so entries expire close to
        their deadline even if the hub is busy.
        """
        while True:
            await asyncio.sleep(_VERDICT_CLEANUP_INTERVAL)
            self._cleanup_stale_verdicts()

    def _fan_out(self, event: ClearanceEvent) -> None:
        """Push *event* to every subscriber queue, dropping oldest on overflow.

        Iterates ``self._subscribers`` directly — the loop body can't
        yield (no ``await``), so no other coroutine can mutate the set
        between iterations and a defensive copy would be pointless.
        """
        for queue in self._subscribers:
            if queue.full():
                with contextlib.suppress(asyncio.QueueEmpty):
                    queue.get_nowait()
            queue.put_nowait(event)

    # ── varlink method implementations ─────────────────────────────────

    async def _subscribe(self) -> AsyncIterator[ClearanceEvent]:
        """Create a per-connection queue and yield events until the client goes.

        The generator is never expected to terminate on its own — ending
        "normally" with ``delay_generator=False`` trips the asyncvarlink
        server-protocol ``assert not continues`` because every yield
        leaves ``continues=True``.  Shutdown runs through
        [`stop`][terok_clearance.hub.server.ClearanceHub.stop]'s ``close_clients()`` instead, which triggers a
        send_reply OSError and a clean ``generator.aclose()`` into the
        ``finally`` block below.

        Known-benign shutdown noise: on Python 3.14 the stop path can
        still log ``ERROR asyncio: Exception in callback
        VarlinkServerProtocol._on_receiver_completes(): CancelledError``.
        That's an asyncvarlink × 3.14 interaction — asyncio.run cancels
        the in-flight handler task, ``CancelledError`` propagates
        through our ``yield await queue.get()``, asyncvarlink's
        completion callback then does ``call_fut.exception()`` which
        in 3.14 re-raises on a cancelled future (older Pythons
        returned ``None``).  Purely cosmetic; the hub has already
        stopped cleanly by the time it logs.  Revisit once
        asyncvarlink wraps that ``exception()`` call in
        ``try/except CancelledError``.
        """
        queue: asyncio.Queue[ClearanceEvent] = asyncio.Queue(maxsize=_SUBSCRIBER_QUEUE_DEPTH)
        self._subscribers.add(queue)
        try:
            while True:
                yield await queue.get()
        finally:
            self._subscribers.discard(queue)

    async def _apply_verdict(self, container: str, request_id: str, dest: str, action: str) -> bool:
        """Validate the triple, shell out to ``terok-shield``, emit VerdictApplied.

        Raises [`InvalidAction`][terok_clearance.hub.server.InvalidAction] / [`UnknownRequest`][terok_clearance.hub.server.UnknownRequest] /
        [`VerdictTupleMismatch`][terok_clearance.hub.server.VerdictTupleMismatch] / [`ShieldCliFailed`][terok_clearance.hub.server.ShieldCliFailed] on the
        four refusal paths; returns ``True`` only when the shield
        invocation itself succeeded.  The ``verdict_applied`` event
        emitted on the shared fan-out carries the same ``ok`` value so
        every subscriber (including the one that called us) sees the
        same outcome.

        On failure the event also carries shield's stderr, sanitised, as
        ``reason``.  The ``ShieldCliFailed`` raised here reaches only the
        caller, and the popups are drawn from the event, not from the
        caller's reply.  Without ``reason`` on the event, an operator
        whose Allow was refused by the security tier would see "Allow
        failed" with no explanation.  Shield's own refusal line says the
        way through is an override, and ``reason`` is how that line
        reaches the popup.
        """
        if action not in VERDICT_ACTIONS:
            raise InvalidAction(action=action)
        live = self._live_verdicts.pop(request_id, None)
        ts = self._live_verdicts_ts.pop(request_id, None)
        if live is None:
            raise UnknownRequest(request_id=request_id)
        expected_container, expected_dest = live
        if expected_container != container or expected_dest != dest:
            # Put it back — a later legitimate verdict on the same request
            # should still be accepted, so this call's mismatch mustn't
            # consume the entry.
            self._live_verdicts[request_id] = live
            if ts is not None:
                self._live_verdicts_ts[request_id] = ts
            raise VerdictTupleMismatch(
                expected_container=expected_container,
                expected_dest=expected_dest,
                got_container=container,
                got_dest=dest,
            )

        ok, stderr_snippet = await self._verdict_client.apply(container, dest, action)
        if not ok:
            # Restore the authz binding so a retry can reach shield — a
            # spawn / timeout / non-zero exit is transient and the next
            # ``Verdict`` on the same ``request_id`` should still land on
            # the same ``(container, dest)`` pair.
            self._live_verdicts[request_id] = live
            if ts is not None:
                self._live_verdicts_ts[request_id] = ts
        # Republish the outcome on the event stream so every subscriber
        # (not just this caller) can flip its notification state — and,
        # on failure, say why.  Everything on the event stream passes through
        # the wire sanitiser, and shield's stderr gets the same treatment.
        self._fan_out(
            ClearanceEvent(
                type="verdict_applied",
                container=container,
                request_id=request_id,
                action=action,
                ok=ok,
                reason="" if ok else sanitize(stderr_snippet, max_len=_VERDICT_REASON_MAX_LEN),
            )
        )
        if not ok:
            raise ShieldCliFailed(action=action, stderr=stderr_snippet)
        return True

    # ── mute state ─────────────────────────────────────────────────────

    def _mute_key(self, container: str) -> str:
        """Return the key *container*'s prompts are muted under.

        A multi-container hub takes the key as given.  A hub built for one
        container maps its aliases onto the short id its events carry and
        raises [`UnknownContainer`][terok_clearance.wire.errors.UnknownContainer]
        for anything else — a mute filed under a key no event carries would
        report success while every prompt kept coming.
        """
        if self._container is None:
            return container
        try:
            return self._mute_keys[container]
        except KeyError:
            raise UnknownContainer(container=container, expected=self._container) from None

    def _seed_mute(self, container: str) -> None:
        """Apply one launch-default mute, or log why it cannot be honoured."""
        try:
            key = self._mute_key(container)
        except UnknownContainer:
            _log.warning(
                "launch default names %s, which this hub (%s) does not serve; prompts stay on",
                container,
                self._container,
            )
            return
        self._muted.add(key)
        _log.info("clearance prompts muted for %s (launch default)", key)

    async def _set_mute(self, container: str, muted: bool) -> bool:
        """Start or stop dropping *container*'s prompts; returns the state in force.

        Muting is not a verdict.  It never allows anything and never denies
        anything: the refusal happened in the kernel before the hub saw the
        event.  What changes is whether an operator is asked about it.  The
        mute does not change whether the refusal is recorded either.  The
        reader writes the audit line while the task's shield audit is
        enabled, prompt or no prompt.  With audit off, a muted refusal
        leaves no record at all.

        Raises [`UnknownContainer`][terok_clearance.wire.errors.UnknownContainer]
        when this hub serves one container and *container* is not one of its
        keys, so a caller holding the wrong identifier finds out.
        """
        try:
            key = self._mute_key(container)
        except UnknownContainer:
            _log.warning(
                "SetMute refused: %s is not this hub's container (%s)", container, self._container
            )
            raise
        if muted:
            self._muted.add(key)
        else:
            self._muted.discard(key)
        _log.info("clearance prompts %s for %s", "muted" if muted else "unmuted", key)
        return key in self._muted

    async def _mutes(self) -> list[str]:
        """Return the muted containers, sorted so equal states give equal replies."""
        return sorted(self._muted)


# ── module-level helpers ───────────────────────────────────────────────


def _translate_reader_event(wire_type: str, raw: dict) -> ClearanceEvent:
    """Build a [`ClearanceEvent`][terok_clearance.ClearanceEvent] from an ingester-parsed dict.

    Every string field is run through
    [`sanitize`][terok_clearance.wire.sanitize.sanitize] on the way in
    — this is the single chokepoint that enforces the wire-format
    invariant for every subscriber, including third-party listeners
    that didn't know they had to escape.  Producers (the shield NFLOG
    reader, the shield CLI) already sanitise on emit as
    belt-and-braces for the container-out path; the duplicate work
    here covers any future emitter (or compromised one) that doesn't.

    The ingester already decoded JSON; this routine just moves fields
    around into the typed shape and normalises missing values.  Keyed
    by ``wire_type`` so each kind gets exactly the fields it needs.
    """
    container = sanitize(str(raw["container"]))
    dossier = _coerce_dossier(raw.get("dossier"))
    if wire_type == "connection_blocked":
        return ClearanceEvent(
            type=wire_type,
            container=container,
            request_id=sanitize(str(raw["id"])),
            dest=sanitize(str(raw["dest"])),
            port=int(raw["port"]),
            proto=int(raw["proto"]),
            domain=sanitize(str(raw.get("domain", ""))),
            dossier=dossier,
        )
    if wire_type == "container_exited":
        return ClearanceEvent(
            type=wire_type,
            container=container,
            reason=sanitize(str(raw.get("reason", ""))),
            dossier=dossier,
        )
    return ClearanceEvent(type=wire_type, container=container, dossier=dossier)


def _coerce_dossier(raw: object) -> Dossier:
    """Pull a sanitised ``Dossier`` out of whatever the reader put on the wire.

    A missing or non-object payload normalises to ``{}`` rather than crashing
    the translator — the hub must absorb a misshaped event the same way it
    drops malformed wire types.  Values are coerced via ``str()`` (the upstream
    JSON allows numbers/bools/null) and then sanitised so every consumer sees
    printable-ASCII strings regardless of which producer emitted them.
    """
    if not isinstance(raw, dict):
        return {}
    return sanitize_mapping({str(k): str(v) for k, v in raw.items()})


def _own_version() -> str:
    """Return our package version for varlink ``GetInfo`` — best-effort."""
    try:
        from importlib.metadata import version

        return version("terok-clearance")
    except Exception:  # pragma: no cover - only hits if metadata is missing
        return "0.0.0"


def _default_reader_socket() -> Path:
    """The EventIngester's default path, re-derived for the hub's wiring."""
    from terok_clearance.hub.ingester import default_socket_path

    return default_socket_path()


# ── stdout bootstrapper (called from _registry._handle_serve) ──────────


async def serve() -> None:  # pragma: no cover - integration path
    """Run the hub service until SIGINT/SIGTERM.

    The entry point ``terok-clearance-hub serve`` hands off here.  Blocks forever
    on a signal-set [`asyncio.Event`][asyncio.Event]; the first SIGINT/SIGTERM
    flips it, then [`stop`][terok_clearance.hub.server.ClearanceHub.stop]
    tears down the server under a timeout.
    """
    from terok_clearance.runtime.service import configure_logging, wait_for_shutdown_signal

    configure_logging()
    hub = ClearanceHub()
    await hub.start()
    try:
        await wait_for_shutdown_signal()
    finally:
        await hub.stop()
