# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Install the terok-dbus systemd user unit and reload the user daemon.

Renders the bundled ``terok-dbus.service`` into
``$XDG_CONFIG_HOME/systemd/user/terok-dbus.service`` with ``{{BIN}}``
replaced by the operator-resolved ``terok-dbus`` invocation.  Matches
the install patterns used by ``terok-credential-proxy`` and
``terok-gate``.
"""

from __future__ import annotations

import os
import shutil
import subprocess  # nosec B404
from importlib import resources as importlib_resources
from pathlib import Path

UNIT_NAME = "terok-dbus.service"

_UNIT_VERSION = 1
"""Bump when the unit template's semantics change.

Substituted into the ``{{UNIT_VERSION}}`` marker at render time so
:func:`check_units_outdated` can tell an installed unit from an older
generation — the varlink hub, for example, ships as v1; any installed
unit without a marker (the pre-varlink Shield1 D-Bus hub) reads as
``None`` and is surfaced as stale.
"""

_VERSION_MARKER_PREFIX = "# terok-dbus-version:"
"""First non-SPDX line in every shipped template; also the parser key."""


def install_service(bin_path: Path | list[str]) -> Path:
    """Render the unit template, write it into the user systemd directory, reload.

    Args:
        bin_path: Either a ``Path`` naming the ``terok-dbus`` launcher
            (a single executable, space-tolerant — e.g. from
            ``shutil.which("terok-dbus")``) or a ``list[str]`` argv
            (the module-fallback form, e.g.
            ``[sys.executable, "-m", "terok_dbus._cli"]``).  Each token
            is quoted individually on render so systemd's whitespace
            tokeniser sees the intended argv boundaries regardless of
            spaces inside any element.

    Returns:
        The on-disk path the unit was written to.
    """
    template = _read_template()
    rendered = template.replace("{{BIN}}", _render_exec_start(bin_path)).replace(
        "{{UNIT_VERSION}}", str(_UNIT_VERSION)
    )
    dest = _user_systemd_dir() / UNIT_NAME
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(rendered)
    _daemon_reload()
    return dest


def _render_exec_start(bin_path: Path | list[str]) -> str:
    """Prepare a ``{{BIN}}`` substitution value suitable for ``ExecStart=``.

    Quotes each argv token individually — spaces inside a single element
    (an install path under ``/home/me/My Tools/``) stay inside one
    token, and whitespace between tokens remains a systemd separator.
    Rejects control characters that would break line semantics in the
    rendered unit.
    """
    tokens = [str(bin_path)] if isinstance(bin_path, Path) else [str(t) for t in bin_path]
    for token in tokens:
        if any(ch in token for ch in ("\n", "\r")):
            raise ValueError(f"bin_path token is not safe to embed in ExecStart=: {token!r}")
    return " ".join(_quote_exec_token(t) for t in tokens)


def _quote_exec_token(token: str) -> str:
    """Wrap *token* in systemd double-quotes when it contains tokeniser-meaningful whitespace."""
    if any(ch.isspace() for ch in token):
        return f'"{_systemd_quote(token)}"'
    return _systemd_quote(token)


def _systemd_quote(value: str) -> str:
    """Escape ``"`` and ``\\`` so *value* can live safely inside a quoted string."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _read_template() -> str:
    """Load the unit template from the installed package's ``resources/systemd``."""
    source = (
        importlib_resources.files("terok_dbus")
        .joinpath("resources")
        .joinpath("systemd")
        .joinpath(UNIT_NAME)
    )
    return source.read_text()


def _user_systemd_dir() -> Path:
    """Resolve ``$XDG_CONFIG_HOME/systemd/user`` (default ``~/.config/systemd/user``)."""
    xdg_config = os.environ.get("XDG_CONFIG_HOME")
    base = Path(xdg_config) if xdg_config else Path.home() / ".config"
    return base / "systemd" / "user"


def _daemon_reload() -> None:
    """Ask the user's systemd to re-read its unit files; silently skip if unavailable."""
    systemctl = shutil.which("systemctl")
    if not systemctl:
        return
    subprocess.run(  # nosec B603
        [systemctl, "--user", "daemon-reload"],
        check=False,
        capture_output=True,
    )


def read_installed_unit() -> str | None:
    """Return the contents of the installed hub unit, or ``None`` if absent."""
    path = _user_systemd_dir() / UNIT_NAME
    try:
        return path.read_text()
    except OSError:
        return None


def read_installed_unit_version() -> int | None:
    """Return the ``# terok-dbus-version:`` stamp of the installed unit.

    Returns ``None`` when the unit is absent or predates the version
    marker (the pre-varlink Shield1 D-Bus hub was shipped without
    one).  ``check_units_outdated`` builds the operator-facing message
    on top of this.
    """
    unit = read_installed_unit()
    if unit is None:
        return None
    for line in unit.splitlines():
        if line.startswith(_VERSION_MARKER_PREFIX):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def check_units_outdated() -> str | None:
    """Return a one-line drift warning if the installed unit is stale, else ``None``.

    ``None`` is returned when nothing is installed — the caller decides
    whether that counts as an error (``terok setup`` skipped) or is
    fine (headless host).  A stale install (older marker, or no
    marker at all) yields a message ending in ``rerun `terok setup```
    so ``sickbay --fix`` has an obvious next action.
    """
    if not (_user_systemd_dir() / UNIT_NAME).is_file():
        return None
    installed = read_installed_unit_version()
    if installed is None or installed < _UNIT_VERSION:
        installed_label = "unversioned" if installed is None else f"v{installed}"
        return (
            f"{UNIT_NAME} is outdated "
            f"(installed {installed_label}, expected v{_UNIT_VERSION}) — rerun `terok setup`."
        )
    return None
