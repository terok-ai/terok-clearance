# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Guard the Tier-A promise of the package barrel.

A bare ``import terok_clearance`` must not drag in the heavy transport
trees — ``asyncvarlink`` (hub/client/verdict) or ``dbus_fast``
(subscriber/notifications).  They load only when the matching public
symbol is first accessed via the PEP 562 ``__getattr__`` seam.
"""

import subprocess
import sys


def test_bare_import_pulls_no_transport_deps():
    """``import terok_clearance`` alone loads neither asyncvarlink nor dbus_fast."""
    probe = (
        "import sys, terok_clearance; "
        "print(sorted({'asyncvarlink', 'dbus_fast'} & set(sys.modules)))"
    )
    result = subprocess.run(
        [sys.executable, "-c", probe],
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stdout.strip() == "[]"
