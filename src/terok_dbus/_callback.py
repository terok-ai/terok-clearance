# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Callback-driven notifier for programmatic consumers.

``CallbackNotifier`` is a headless ``Notifier`` backend that invokes
user-supplied callables instead of rendering UI.  It enables any
consumer — Textual TUI, web dashboard, CLI tool — to build its own
presentation on top of the ``EventSubscriber`` signal pipeline without
depending on a D-Bus desktop notification daemon.

Typical usage::

    notifier = CallbackNotifier(on_notify=my_handler)
    subscriber = EventSubscriber(notifier)
    await subscriber.start()
"""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any


@dataclass
class Notification:
    """Snapshot of a single notification posted by the subscriber."""

    nid: int
    summary: str
    body: str
    actions: list[tuple[str, str]]
    replaces_id: int
    timeout_ms: int


class CallbackNotifier:
    """``Notifier`` backend that delegates rendering to caller-supplied hooks.

    Args:
        on_notify: Called for every ``notify()`` with a :class:`Notification`.
            Receives new notifications (``replaces_id == 0``) and in-place
            updates (``replaces_id > 0``, e.g. verdict results).
    """

    def __init__(
        self,
        on_notify: Callable[[Notification], None] | None = None,
    ) -> None:
        """Bind optional notify callback."""
        self._on_notify = on_notify
        self._next_id = 1
        self._callbacks: dict[int, Callable[[str], None]] = {}

    async def notify(
        self,
        summary: str,
        body: str = "",
        *,
        actions: Sequence[tuple[str, str]] = (),
        timeout_ms: int = -1,
        hints: Mapping[str, Any] | None = None,
        replaces_id: int = 0,
        app_icon: str = "",
    ) -> int:
        """Record the notification and invoke the ``on_notify`` hook.

        Returns a monotonically increasing ID, or *replaces_id* for updates.
        """
        nid = replaces_id if replaces_id else self._next_id
        if not replaces_id:
            self._next_id += 1
        notification = Notification(
            nid=nid,
            summary=summary,
            body=body,
            actions=list(actions),
            replaces_id=replaces_id,
            timeout_ms=timeout_ms,
        )
        if self._on_notify:
            self._on_notify(notification)
        return nid

    async def on_action(
        self,
        notification_id: int,
        callback: Callable[[str], None],
    ) -> None:
        """Store the action callback for later invocation."""
        self._callbacks[notification_id] = callback

    async def close(self, notification_id: int) -> None:
        """Remove the callback for a closed notification."""
        self._callbacks.pop(notification_id, None)

    async def disconnect(self) -> None:
        """Release all stored callbacks."""
        self._callbacks.clear()

    def invoke_action(self, notification_id: int, action_key: str) -> None:
        """Invoke the stored callback for a user verdict.

        This is the entry point for consumers that handle user input
        (Allow/Deny) and need to route the decision back through
        ``EventSubscriber`` to the D-Bus ``Verdict``/``Resolve`` method.
        """
        if cb := self._callbacks.pop(notification_id, None):
            cb(action_key)
