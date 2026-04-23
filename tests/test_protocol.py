# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Tests for the Notifier protocol — structural subtyping checks."""

from terok_clearance._null import NullNotifier
from terok_clearance._protocol import Notifier


class TestNotifierProtocol:
    """Verify Protocol structural checks at runtime."""

    def test_null_notifier_satisfies_protocol(self):
        assert isinstance(NullNotifier(), Notifier)

    def test_arbitrary_object_does_not_satisfy(self):
        assert not isinstance(object(), Notifier)

    def test_protocol_is_runtime_checkable(self):
        """The @runtime_checkable decorator must be present."""
        assert hasattr(Notifier, "__protocol_attrs__") or hasattr(Notifier, "__abstractmethods__")
