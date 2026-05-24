# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Install the clearance hub + verdict helper + desktop-notifier systemd user units.

Two installable systemd services, exposed as classes:

* [`HubService`][terok_clearance.runtime.installer.HubService] — the
  hub varlink server paired with the verdict helper.  Installed
  together: hub serves and dispatches, verdict execs
  ``terok-shield allow|deny``.  Both run the same launcher with
  different subcommands, so one ``bin_path`` configures both units.
* [`NotifierService`][terok_clearance.runtime.installer.NotifierService]
  — the standalone D-Bus desktop notifier.  Optional separate install:
  headless hosts may skip it; desktop hosts install it on top of the
  hub pair.

[`outdated_summary`][terok_clearance.runtime.installer.outdated_summary]
aggregates the two services' drift warnings into a single one-line
message — the shape ``terok sickbay`` and similar consumers want.

All unit files render from templates under ``resources/systemd``;
``{{BIN}}`` and ``{{UNIT_VERSION}}`` substitution happens at render
time.  Both services are hardened where systemd allows it (the hub
runs under NNP + seccomp + mount-ns isolation); the verdict helper
is unhardened because podman setns requires it.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess  # nosec B404 — installer drives systemctl on the host
import sys
from dataclasses import dataclass
from importlib import resources as importlib_resources
from pathlib import Path
from typing import ClassVar

#: Default argv for the hub launcher — ``python -m`` the CLI entrypoint.
#:
#: Sandbox used to pass this argv explicitly; baking it in lets
#: callers invoke [`HubService.install`][terok_clearance.runtime.installer.HubService.install]
#: bare.  ``sys.executable`` skips PATH resolution (a hostile PATH
#: can't poison the rendered ``ExecStart=``) and lands on the same
#: venv's python that owns the installed clearance package.
_DEFAULT_HUB_ARGV: tuple[str, ...] = (sys.executable, "-m", "terok_clearance.cli.main")

#: Default argv for the notifier launcher — same reasoning as above.
_DEFAULT_NOTIFIER_ARGV: tuple[str, ...] = (sys.executable, "-m", "terok_clearance.notifier.app")

#: Unit-file names ``HubService`` / ``NotifierService`` own.  Each
#: renders from a template under ``resources/systemd``;
#: ``{{UNIT_VERSION}}`` + ``{{BIN}}`` substitution happens at render
#: time.
HUB_UNIT_NAME = "terok-clearance-hub.service"
VERDICT_UNIT_NAME = "terok-clearance-verdict.service"
NOTIFIER_UNIT_NAME = "terok-clearance-notifier.service"

#: Phrasing tail appended to every drift message — frontend-agnostic
#: so clearance doesn't have to guess which CLI the operator uses to
#: reinstall (``terok setup``, ``terok-executor setup``, a
#: ``terok-clearance`` verb when one ships).  "your clearance setup
#: command" is vague by design.
_RERUN_HINT = "rerun your clearance setup command"


# ── Per-unit version probe ──────────────────────────────


@dataclass(frozen=True)
class _UnitMarker:
    """A systemd unit file paired with the version marker comment it carries.

    Each installed unit embeds a single ``# terok-clearance-<role>-version: N``
    line so the installer can answer "is the installed file behind the
    bundled template?" without re-parsing the whole unit.  Bumping
    ``expected_version`` here and in the template's ``{{UNIT_VERSION}}``
    placeholder is the signal that a reinstall is required.
    """

    unit_name: str
    """File name under ``$XDG_CONFIG_HOME/systemd/user`` (e.g. ``terok-clearance-hub.service``)."""

    marker_prefix: str
    """Comment-line prefix the embedded version stamp follows (e.g. ``# terok-clearance-hub-version:``)."""

    expected_version: int
    """Bundled template's version stamp.  Bumped when the template's semantics change."""

    def installed_version(self) -> int | None:
        """Return the version stamp from the installed unit, or ``None``.

        ``None`` means either "unit not installed" or "unit installed
        without a marker".  Callers that need to distinguish use
        [`is_installed`][terok_clearance.runtime.installer._UnitMarker.is_installed].
        """
        path = _user_systemd_dir() / self.unit_name
        try:
            text = path.read_text()
        except OSError:
            return None
        for line in text.splitlines():
            if line.startswith(self.marker_prefix):
                try:
                    return int(line.split(":", 1)[1].strip())
                except ValueError:
                    return None
        return None

    def is_installed(self) -> bool:
        """True when the unit file exists on disk (with or without a version marker)."""
        return (_user_systemd_dir() / self.unit_name).is_file()

    def drift_warning(self) -> str | None:
        """Return a stale-unit warning vs ``expected_version``, or ``None`` if current.

        Treats "unversioned" (file present but no marker line) as
        outdated so a manual edit can't silently freeze the install
        at an old shape.
        """
        installed = self.installed_version()
        if installed is None or installed < self.expected_version:
            installed_label = "unversioned" if installed is None else f"v{installed}"
            return (
                f"{self.unit_name} is outdated "
                f"(installed {installed_label}, expected v{self.expected_version}) — "
                f"{_RERUN_HINT}."
            )
        return None


# ── Hub + verdict pair ──────────────────────────────────


class HubService:
    """The clearance hub + verdict-helper pair as one installable service.

    The pair is installed together: the hub varlink server fans out to
    subscribers and binds operator authz, the verdict helper execs
    ``terok-shield allow|deny`` for routed verdicts.  Both run the
    same launcher with different subcommands (``serve`` vs
    ``serve-verdict``), so a single ``bin_path`` configures both
    units.

    Lifecycle is symmetric: [`install`][terok_clearance.runtime.installer.HubService.install]
    writes; [`uninstall`][terok_clearance.runtime.installer.HubService.uninstall]
    removes.  Both daemon-reload at the end so systemd picks up the
    change.
    """

    #: Bumped when the hub/verdict templates change semantics
    #: (hardening directives, socket paths, argv shape).
    #: ``NotifierService.UNIT_VERSION`` evolves independently —
    #: notifier-only edits don't falsely report hub/verdict as stale,
    #: and vice versa.
    UNIT_VERSION: ClassVar[int] = 3

    #: Unit file names this service owns, in template-render order.
    UNIT_NAMES: ClassVar[tuple[str, ...]] = (HUB_UNIT_NAME, VERDICT_UNIT_NAME)

    _HUB_MARKER: ClassVar[_UnitMarker] = _UnitMarker(
        unit_name=HUB_UNIT_NAME,
        marker_prefix="# terok-clearance-hub-version:",
        expected_version=UNIT_VERSION,
    )
    _VERDICT_MARKER: ClassVar[_UnitMarker] = _UnitMarker(
        unit_name=VERDICT_UNIT_NAME,
        marker_prefix="# terok-clearance-verdict-version:",
        expected_version=UNIT_VERSION,
    )

    @classmethod
    def install(cls, bin_path: Path | list[str] | None = None) -> tuple[Path, Path]:
        """Render + write both unit files into the user systemd directory.

        Calls ``systemctl --user daemon-reload`` once at the end.

        Args:
            bin_path: ``Path`` to the launcher, or a ``list[str]`` argv.
                ``None`` (the default) renders
                ``python -m terok_clearance.cli.main`` against the
                running interpreter — the shape pipx installs use —
                so callers don't need to spell clearance's own module
                layout.

        Returns:
            ``(hub_path, verdict_path)`` — the on-disk paths of the
            two unit files.
        """
        bin_rendered = _render_exec_start(
            bin_path if bin_path is not None else list(_DEFAULT_HUB_ARGV)
        )
        dest_dir = _user_systemd_dir()
        dest_dir.mkdir(parents=True, exist_ok=True)
        paths: list[Path] = []
        for unit_name in cls.UNIT_NAMES:
            template = _read_template(unit_name)
            rendered = template.replace("{{UNIT_VERSION}}", str(cls.UNIT_VERSION)).replace(
                "{{BIN}}", bin_rendered
            )
            dest = dest_dir / unit_name
            dest.write_text(rendered)
            paths.append(dest)
        _daemon_reload()
        return paths[0], paths[1]

    @classmethod
    def uninstall(cls) -> None:
        """Disable + unlink the hub + verdict units; daemon-reload once.

        All individual steps soft-fail so a half-installed tree still
        ends up clean.
        """
        for name in cls.UNIT_NAMES:
            _disable_and_unlink(name)
        _daemon_reload()

    @classmethod
    def installed_version(cls) -> int | None:
        """Return the hub unit's stamp, or ``None`` if not installed.

        ``None`` is either "unit not installed" or "unit installed
        without a marker"; [`outdated_warning`][terok_clearance.runtime.installer.HubService.outdated_warning]
        differentiates between those in its operator-facing message.
        """
        return cls._HUB_MARKER.installed_version()

    @classmethod
    def outdated_warning(cls) -> str | None:
        """Return a one-line drift warning for the pair, or ``None`` when healthy.

        Reports the first stale unit found, treating a half-installed
        pair (one unit present, one missing) as stale so the operator
        is prompted to restore symmetry.  Returns ``None`` when both
        units are absent (the headless case) or both are current.
        """
        markers = (cls._HUB_MARKER, cls._VERDICT_MARKER)
        present = {m.unit_name: m.is_installed() for m in markers}
        for marker in markers:
            if not present[marker.unit_name]:
                continue
            if (warning := marker.drift_warning()) is not None:
                return warning
        if any(present.values()) and not all(present.values()):
            missing = ", ".join(name for name, ok in present.items() if not ok)
            return (
                f"half-installed: missing {missing} — "
                f"{_RERUN_HINT} to restore the hub/verdict pair."
            )
        return None


# ── Standalone desktop notifier ─────────────────────────


class NotifierService:
    """The clearance desktop-notifier as a standalone installable service.

    Optional install: headless hosts skip it entirely; desktop hosts
    add it on top of [`HubService`][terok_clearance.runtime.installer.HubService]
    by calling only this class's [`install`][terok_clearance.runtime.installer.NotifierService.install].
    Versioned independently of the hub pair so each install target
    can evolve on its own cadence — the three units ship different
    ExecStart shapes, hardening profiles, and session-bus dependencies.
    """

    #: Bumped when the notifier template changes semantics (ExecStart,
    #: hardening directives, session-bus dependency).
    UNIT_VERSION: ClassVar[int] = 5

    #: Unit file names this service owns — a one-tuple, kept parallel
    #: to [`HubService.UNIT_NAMES`][terok_clearance.runtime.installer.HubService.UNIT_NAMES]
    #: so the two services have the same shape for callers iterating
    #: over them.
    UNIT_NAMES: ClassVar[tuple[str, ...]] = (NOTIFIER_UNIT_NAME,)

    _MARKER: ClassVar[_UnitMarker] = _UnitMarker(
        unit_name=NOTIFIER_UNIT_NAME,
        marker_prefix="# terok-clearance-notifier-version:",
        expected_version=UNIT_VERSION,
    )

    @classmethod
    def install(cls, bin_path: Path | list[str] | None = None) -> Path:
        """Render + write the notifier unit into the user systemd directory.

        Paired with [`HubService.install`][terok_clearance.runtime.installer.HubService.install]:
        headless hosts that installed the hub pair can opt into the
        desktop notifier later by calling only this method.
        Daemon-reloads once at the end.

        Args:
            bin_path: ``Path`` to the notifier launcher, or a
                ``list[str]`` argv.  ``None`` (the default) renders
                ``python -m terok_clearance.notifier.app`` against the
                running interpreter.

        Returns:
            The on-disk path of the written unit file.
        """
        bin_rendered = _render_exec_start(
            bin_path if bin_path is not None else list(_DEFAULT_NOTIFIER_ARGV)
        )
        dest_dir = _user_systemd_dir()
        dest_dir.mkdir(parents=True, exist_ok=True)
        template = _read_template(NOTIFIER_UNIT_NAME)
        rendered = template.replace("{{UNIT_VERSION}}", str(cls.UNIT_VERSION)).replace(
            "{{BIN}}", bin_rendered
        )
        dest = dest_dir / NOTIFIER_UNIT_NAME
        dest.write_text(rendered)
        _daemon_reload()
        return dest

    @classmethod
    def uninstall(cls) -> None:
        """Disable + unlink the notifier unit; daemon-reload once.

        Soft-fail on every step so a half-installed tree still ends
        up clean.
        """
        _disable_and_unlink(NOTIFIER_UNIT_NAME)
        _daemon_reload()

    @classmethod
    def installed_version(cls) -> int | None:
        """Return the notifier unit's version stamp, or ``None`` if not installed."""
        return cls._MARKER.installed_version()

    @classmethod
    def outdated_warning(cls) -> str | None:
        """Return a stale-unit warning, or ``None`` when absent or current."""
        if not cls._MARKER.is_installed():
            return None
        return cls._MARKER.drift_warning()


# ── Cross-service aggregator ────────────────────────────


def outdated_summary() -> str | None:
    """Return the first stale-unit warning across hub and notifier, or ``None``.

    Composes [`HubService.outdated_warning`][terok_clearance.runtime.installer.HubService.outdated_warning]
    and [`NotifierService.outdated_warning`][terok_clearance.runtime.installer.NotifierService.outdated_warning]
    so a single call gives operator-diagnostic UIs (``terok sickbay``)
    the worst-case status without their having to know about the two
    services separately.
    """
    return HubService.outdated_warning() or NotifierService.outdated_warning()


# ── systemd unit-file mechanics ─────────────────────────


def _disable_and_unlink(unit_name: str) -> None:
    """``systemctl --user disable --now <unit>`` + unlink — soft-fail on every step.

    Always runs ``disable`` even when the unit file is already missing — an
    operator who manually ``rm``'d the file can still have dangling
    ``default.target.wants/`` symlinks that ``disable`` will clear.
    """
    path = _user_systemd_dir() / unit_name
    systemctl = shutil.which("systemctl")
    if systemctl:
        with contextlib.suppress(Exception):
            subprocess.run(  # nosec B603
                [systemctl, "--user", "disable", "--now", unit_name],
                check=False,
                capture_output=True,
            )
    with contextlib.suppress(OSError):
        path.unlink()


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


def _read_template(unit_name: str) -> str:
    """Load the named unit template from the package's ``resources/systemd``."""
    source = (
        importlib_resources.files("terok_clearance")
        .joinpath("resources")
        .joinpath("systemd")
        .joinpath(unit_name)
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
