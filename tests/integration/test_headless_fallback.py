# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Story: no bus, no problem.

When no D-Bus session bus is available the library must degrade
gracefully — ``create_notifier()`` returns a ``NullNotifier`` and
the CLI prints ``0`` without crashing.
"""

import subprocess
import sys

import pytest

from terok_clearance import NullNotifier, create_notifier


class TestHeadlessFallback:
    """Verify graceful degradation without a session bus."""

    async def test_create_notifier_returns_null(self, monkeypatch: pytest.MonkeyPatch):
        """Factory returns NullNotifier when bus address is empty."""
        monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
        notifier = await create_notifier()
        assert isinstance(notifier, NullNotifier)

    async def test_null_notify_returns_zero(self, monkeypatch: pytest.MonkeyPatch):
        """NullNotifier.notify() returns 0."""
        monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
        notifier = await create_notifier()
        nid = await notifier.notify("headless test")
        assert nid == 0

    def test_cli_prints_zero_without_bus(self):
        """terok-clearance-notify prints '0' and exits 0 when bus is absent."""
        env = {k: v for k, v in __import__("os").environ.items() if k != "DBUS_SESSION_BUS_ADDRESS"}
        result = subprocess.run(
            [sys.executable, "-m", "terok_clearance._cli", "notify", "Headless", "Test"],
            capture_output=True,
            text=True,
            timeout=10,
            env=env,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == "0"
