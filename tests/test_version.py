# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Smoke test — verify the package is importable."""

import terok_clearance


def test_package_is_importable():
    """The terok_clearance package should be importable and expose __version__."""
    assert hasattr(terok_clearance, "__version__")
    assert isinstance(terok_clearance.__version__, str)
    assert terok_clearance.__version__.strip(), "__version__ should be non-empty"
