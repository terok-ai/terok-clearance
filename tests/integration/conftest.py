# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Fixtures for integration tests — powered by python-dbusmock.

Uses ``dbusmock_session`` to launch a **private** ``dbus-daemon`` so
test notifications never appear in the developer's notification bar.
The ``notification_daemon`` fixture spawns the built-in
``notification_daemon`` template on that bus — pure Python, no
external binaries beyond ``dbus-daemon`` itself.
"""

import os
import shutil
import subprocess
from collections.abc import AsyncIterator, Iterator

import pytest
from dbusmock import SpawnedMock

from terok_clearance import create_notifier
from terok_clearance.notifications.desktop import DbusNotifier
from terok_clearance.notifications.protocol import Notifier

# Activate dbusmock's pytest fixtures (dbusmock_session, dbusmock_system).
pytest_plugins = "dbusmock.pytest_fixtures"


# ── Matrix capability contract ───────────────────────────────────────
# The private-bus fixtures need exactly one external binary: dbus-daemon.
# On a dev machine its absence is a host limitation; inside the matrix
# the harness built the image, so TEROK_EXPECT (exported by the
# matrix engine) makes absence fail the session up front instead of
# every test erroring or skipping in a way that reads as green.
_CAPABILITY_PROBES = {
    "dbus-daemon": lambda: shutil.which("dbus-daemon") is not None,
}


def pytest_sessionstart(session: pytest.Session) -> None:
    """Fail the whole session when the matrix capability contract is broken."""
    expected = {cap for cap in os.environ.get("TEROK_EXPECT", "").split(",") if cap}
    if not expected:
        return
    unknown = expected - _CAPABILITY_PROBES.keys()
    if unknown:
        pytest.exit(f"TEROK_EXPECT names unknown capabilities: {sorted(unknown)}", returncode=3)
    missing = sorted(cap for cap in expected if not _CAPABILITY_PROBES[cap]())
    if missing:
        pytest.exit(
            "matrix capability contract broken — expected but missing: " + ", ".join(missing),
            returncode=3,
        )


@pytest.fixture(scope="session")
def notification_daemon(dbusmock_session) -> Iterator[subprocess.Popen]:
    """Spawn a mock notification daemon on the private session bus.

    Uses the built-in ``notification_daemon`` template from python-dbusmock
    which implements the full ``org.freedesktop.Notifications`` interface.
    """
    mock = SpawnedMock.spawn_with_template("notification_daemon")
    yield mock.process
    mock.process.terminate()
    mock.process.wait()


@pytest.fixture
async def notifier(dbusmock_session, notification_daemon) -> AsyncIterator[Notifier]:
    """Provide a connected ``DbusNotifier`` backed by the private test bus.

    Disconnects automatically after the test.
    """
    n = await create_notifier()
    if not isinstance(n, DbusNotifier):
        pytest.skip("D-Bus notifier backend unavailable in integration environment")
    yield n
    await n.disconnect()
