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


def _run(code: str) -> str:
    """Run *code* in a fresh interpreter and return its stripped stdout."""
    result = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def test_bare_import_pulls_no_transport_deps():
    """``import terok_clearance`` alone loads neither asyncvarlink nor dbus_fast."""
    probe = (
        "import sys, terok_clearance; "
        "print(sorted({'asyncvarlink', 'dbus_fast'} & set(sys.modules)))"
    )
    assert _run(probe) == "[]"


def test_verb_help_imports_only_that_verb():
    """``terok-clearance-hub notify --help`` loads the notify verb, no others."""
    probe = (
        "import sys, io, contextlib\n"
        "sys.argv = ['terok-clearance-hub', 'notify', '--help']\n"
        "import terok_clearance.cli.main as m\n"
        "try:\n"
        "    with contextlib.redirect_stdout(io.StringIO()):\n"
        "        m.main()\n"
        "except SystemExit:\n"
        "    pass\n"
        "loaded = [v for v in ('notify', 'serve', 'serve_verdict', 'clearance')\n"
        "          if f'terok_clearance.cli.verbs.{v}' in sys.modules]\n"
        "print(loaded)"
    )
    assert _run(probe) == "['notify']"


def test_top_help_lists_all_verbs_without_importing_them():
    """Bare ``--help`` lists every verb as a placeholder, importing none of them."""
    probe = (
        "import sys, io, contextlib\n"
        "sys.argv = ['terok-clearance-hub', '--help']\n"
        "import terok_clearance.cli.main as m\n"
        "out = io.StringIO()\n"
        "try:\n"
        "    with contextlib.redirect_stdout(out):\n"
        "        m.main()\n"
        "except SystemExit:\n"
        "    pass\n"
        "text = out.getvalue()\n"
        "listed = all(v in text for v in ('notify', 'serve', 'serve-verdict', 'clearance'))\n"
        "imported = [v for v in ('notify', 'serve', 'serve_verdict', 'clearance')\n"
        "            if f'terok_clearance.cli.verbs.{v}' in sys.modules]\n"
        "print(listed, imported)"
    )
    assert _run(probe) == "True []"
