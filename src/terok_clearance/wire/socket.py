# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Shared socket-hardening helpers for every AF_UNIX server in this package.

The ingester + clearance-hub binds need the same private-parent check,
0600-via-umask, and post-bind ``lstat`` confirmation.  One copy here
means one place to review if the security posture needs to change.
"""

from __future__ import annotations

import contextlib
import os
import stat
from collections.abc import Callable
from contextlib import AbstractContextManager
from pathlib import Path
from typing import Any

#: Umask applied during ``bind()`` so the socket is 0600 the moment it
#: exists — no TOCTOU window where another peer could connect before we
#: chmod it down.
_BIND_UMASK = 0o177

#: Canonical clearance-socket basename under ``$XDG_RUNTIME_DIR``.  Lives
#: with the socket helpers because both identify where to connect on the
#: wire — the interface name (the varlink-level namespace) lives next
#: door in ``wire.interface``.
_CLEARANCE_SOCKET_BASENAME = "terok-clearance.sock"


def runtime_socket_path(basename: str) -> Path:
    """Return ``$XDG_RUNTIME_DIR/<basename>`` with a ``/run/user/<uid>`` fallback."""
    xdg = os.environ.get("XDG_RUNTIME_DIR") or f"/run/user/{os.getuid()}"
    return Path(xdg) / basename


def default_clearance_socket_path() -> Path:
    """Return the canonical clearance-socket path under ``$XDG_RUNTIME_DIR``."""
    return runtime_socket_path(_CLEARANCE_SOCKET_BASENAME)


def ensure_private_parent(path: Path, label: str) -> None:
    """Refuse to bind under a parent that isn't uid-owned + mode 0700-ish.

    *label* is interpolated into the error message so the operator can
    tell at a glance which socket raised.  Creates the parent on the
    first pass (with ``mode=0o700``) so a fresh XDG runtime dir also
    works.
    """
    parent = path.parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    st = parent.stat()
    if st.st_uid != os.getuid():
        raise RuntimeError(
            f"{label} parent dir not owned by current uid: {parent} (owner uid={st.st_uid})"
        )
    if st.st_mode & 0o077:
        raise RuntimeError(
            f"{label} parent dir is group/world accessible: "
            f"{parent} (mode={oct(st.st_mode & 0o777)})"
        )


async def bind_hardened(  # noqa: ANN401 — returns whatever the factory gave us
    factory: Callable[[str], Any],
    path: Path,
    label: str,
    *,
    socket_context: Callable[[], AbstractContextManager[None]] | None = None,
) -> Any:
    """Bind a unix-socket server via *factory* with the full hardening ritual.

    Verifies the parent, unlinks any stale socket path, sets umask
    ``0o177`` so ``bind()`` produces a 0600 file atomically, and
    confirms the path is a socket afterwards.  *factory* is awaited
    with the socket path as its sole argument and must return the
    server object.

    *socket_context* — optional zero-arg callable returning a context
    manager that's entered around the ``await factory(...)`` call.
    Lets a caller install a per-thread socket-creation context (e.g.
    SELinux ``setsockcreatecon`` so containers may ``connectto`` the
    bound socket) without making this package aware of the labelling
    mechanism.  Default ``None`` — no-op wrapper, same behaviour as
    before.
    """
    ensure_private_parent(path, label)
    with contextlib.suppress(FileNotFoundError):
        path.unlink()
    # ``socket_context()`` is called *inside* the try-finally — if its
    # construction raises, the umask must still be restored.
    old_umask = os.umask(_BIND_UMASK)
    try:
        ctx: AbstractContextManager[None] = (
            socket_context() if socket_context is not None else contextlib.nullcontext()
        )
        with ctx:
            server = await factory(str(path))
    finally:
        os.umask(old_umask)
    lst = os.lstat(path)
    if not stat.S_ISSOCK(lst.st_mode):
        raise RuntimeError(f"{label} path is not a socket after bind: {path}")
    return server
