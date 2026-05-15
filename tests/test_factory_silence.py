# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Headless servers run this code path on every notification.

A WARNING per call would flood operational logs and bury real issues;
the design contract is therefore "silent fallback" — the diagnostic
line in
[`create_notifier`][terok_clearance.notifications.factory.create_notifier]
sits at ``DEBUG`` so cron jobs, CI runners, and SSH-only hosts can
import the package without their loggers gaining a steady drip of
"D-Bus session bus unavailable" rows.

This lives at the unit-test root (not under ``tests/integration/``)
because the integration conftest pulls in ``dbusmock``, which needs
native ``python3-dbus`` bindings — verifying log silence requires
neither.
"""

from __future__ import annotations

import logging

import pytest

from terok_clearance import NullNotifier, create_notifier


class TestFactorySilenceOnHeadless:
    """The factory's fallback path stays at DEBUG, never WARNING+."""

    async def test_no_warnings_when_bus_absent(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ):
        """No DBUS_SESSION_BUS_ADDRESS → silent fallback, NullNotifier returned."""
        monkeypatch.delenv("DBUS_SESSION_BUS_ADDRESS", raising=False)
        with caplog.at_level(logging.DEBUG, logger="terok_clearance.notifications.factory"):
            notifier = await create_notifier()
        assert isinstance(notifier, NullNotifier)
        elevated = [r for r in caplog.records if r.levelno >= logging.WARNING]
        assert elevated == [], (
            "Headless fallback must stay silent at WARNING+; got: "
            f"{[(r.levelname, r.getMessage()) for r in elevated]}"
        )
