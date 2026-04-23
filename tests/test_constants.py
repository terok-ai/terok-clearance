# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for D-Bus addressing constants and close-reason codes."""

from terok_clearance._constants import (
    BUS_NAME,
    INTERFACE_NAME,
    OBJECT_PATH,
    CloseReason,
)


class TestDbusConstants:
    """Verify D-Bus addressing strings match the freedesktop spec."""

    def test_bus_name(self):
        assert BUS_NAME == "org.freedesktop.Notifications"

    def test_object_path(self):
        assert OBJECT_PATH == "/org/freedesktop/Notifications"

    def test_interface_name(self):
        assert INTERFACE_NAME == "org.freedesktop.Notifications"


class TestCloseReason:
    """Verify CloseReason enum values match the freedesktop spec."""

    def test_expired(self):
        assert CloseReason.EXPIRED == 1

    def test_dismissed(self):
        assert CloseReason.DISMISSED == 2

    def test_closed(self):
        assert CloseReason.CLOSED == 3

    def test_undefined(self):
        assert CloseReason.UNDEFINED == 4

    def test_is_int(self):
        assert isinstance(CloseReason.EXPIRED, int)
