# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Fixtures for integration tests — powered by python-dbusmock.

Uses ``dbusmock_session`` to launch a **private** ``dbus-daemon`` so
test notifications never appear in the developer's notification bar.
The ``notification_daemon`` fixture spawns the built-in
``notification_daemon`` template on that bus — pure Python, no
external binaries beyond ``dbus-daemon`` itself.
"""

import subprocess
from collections.abc import AsyncIterator, Iterator

import pytest
from dbusmock import SpawnedMock

from terok_dbus import DbusNotifier, Notifier, create_notifier

# Activate dbusmock's pytest fixtures (dbusmock_session, dbusmock_system).
pytest_plugins = "dbusmock.pytest_fixtures"


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
