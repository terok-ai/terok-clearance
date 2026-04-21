# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for CallbackNotifier — headless notifier with user-supplied hooks."""

from unittest.mock import Mock

import pytest

from terok_dbus._callback import CallbackNotifier, Notification
from terok_dbus._protocol import Notifier


@pytest.fixture
def notifier() -> CallbackNotifier:
    """A fresh CallbackNotifier with no hooks."""
    return CallbackNotifier()


class TestCallbackNotifier:
    """CallbackNotifier must satisfy the Notifier protocol."""

    def test_satisfies_notifier_protocol(self, notifier: CallbackNotifier) -> None:
        """Structural compatibility with the Notifier protocol."""
        assert isinstance(notifier, Notifier)

    async def test_notify_returns_monotonic_ids(self, notifier: CallbackNotifier) -> None:
        """Each notify() call returns a unique incrementing ID."""
        id1 = await notifier.notify("A")
        id2 = await notifier.notify("B")
        assert id1 < id2

    async def test_notify_replaces_id(self, notifier: CallbackNotifier) -> None:
        """replaces_id reuses the given ID."""
        nid = await notifier.notify("update", replaces_id=42)
        assert nid == 42

    async def test_notify_invokes_hook(self) -> None:
        """The on_notify hook receives a Notification dataclass."""
        received: list[Notification] = []
        notifier = CallbackNotifier(on_notify=received.append)
        await notifier.notify("Title", "Body", actions=[("accept", "Allow")])
        assert len(received) == 1
        assert received[0].summary == "Title"
        assert received[0].body == "Body"
        assert received[0].actions == [("accept", "Allow")]

    async def test_notify_without_hook(self, notifier: CallbackNotifier) -> None:
        """notify() works fine without an on_notify hook."""
        nid = await notifier.notify("Title")
        assert nid >= 1

    async def test_on_action_stores_callback(self, notifier: CallbackNotifier) -> None:
        """on_action() stores a callback for later invocation."""
        cb = Mock()
        await notifier.on_action(1, cb)
        assert 1 in notifier._callbacks

    def test_invoke_action_calls_and_removes(self, notifier: CallbackNotifier) -> None:
        """invoke_action() calls the stored callback and removes it."""
        cb = Mock()
        notifier._callbacks[5] = cb
        notifier.invoke_action(5, "accept")
        cb.assert_called_once_with("accept")
        assert 5 not in notifier._callbacks

    def test_invoke_action_noop_for_unknown(self, notifier: CallbackNotifier) -> None:
        """invoke_action() is a no-op for unknown IDs."""
        notifier.invoke_action(999, "deny")  # should not raise

    async def test_close_removes_callback(self, notifier: CallbackNotifier) -> None:
        """close() removes the callback for a notification."""
        await notifier.on_action(3, Mock())
        await notifier.close(3)
        assert 3 not in notifier._callbacks

    async def test_disconnect_clears_all(self, notifier: CallbackNotifier) -> None:
        """disconnect() removes all callbacks."""
        await notifier.on_action(1, Mock())
        await notifier.on_action(2, Mock())
        await notifier.disconnect()
        assert len(notifier._callbacks) == 0

    def test_on_container_started_forwards_to_hook(self) -> None:
        """Lifecycle hook is invoked with the container id when bound."""
        hook = Mock()
        notifier = CallbackNotifier(on_container_started=hook)
        notifier.on_container_started("abc123")
        hook.assert_called_once_with("abc123")

    def test_on_container_exited_forwards_to_hook(self) -> None:
        """Lifecycle hook is invoked with (container, reason) when bound."""
        hook = Mock()
        notifier = CallbackNotifier(on_container_exited=hook)
        notifier.on_container_exited("abc123", "poststop")
        hook.assert_called_once_with("abc123", "poststop")

    def test_lifecycle_without_hook_is_noop(self) -> None:
        """No-hook is fine — the methods exist so the subscriber can probe them."""
        notifier = CallbackNotifier()
        notifier.on_container_started("abc123")  # must not raise
        notifier.on_container_exited("abc123", "poststop")  # must not raise


class TestNotification:
    """Tests for the Notification dataclass."""

    def test_fields(self) -> None:
        """Notification stores all expected fields."""
        n = Notification(nid=1, summary="S", body="B", actions=[], replaces_id=0, timeout_ms=-1)
        assert n.nid == 1
        assert n.summary == "S"
        assert n.body == "B"
