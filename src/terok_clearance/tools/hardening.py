# SPDX-FileCopyrightText: 2026 Jiri Vyskocil
# SPDX-License-Identifier: Apache-2.0

"""Clearance hardening installer — separate from the daily user CLI.

Mirror of ``terok_sandbox.tools.hardening`` for the clearance hub +
notifier domains.  Tooling, not a feature: lives under ``tools/``
because the optional MAC layer is something a *packager* installs
(deb / rpm postinst, ansible, …), not something users run as part
of normal use.  In dev / pipx deployments the operator invokes it
manually:

    python -m terok_clearance.tools.hardening install
    python -m terok_clearance.tools.hardening remove
    python -m terok_clearance.tools.hardening status

The duplication between this module and ``terok_sandbox.tools.hardening``
is by design: terok-clearance is a leaf package (cannot import from
terok-sandbox per AGENTS.md), and the install sequences are short
enough that one self-contained function per package is more legible
than a shared helper layer would be.

Replaces the older ``resources/install_hardening.sh`` shell flow.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from importlib.resources import files as _resource_files
from pathlib import Path

# ---------- Configuration ----------

_RES = _resource_files("terok_clearance.resources")

SELINUX_MODULES: tuple[Path, ...] = (
    Path(str(_RES / "selinux/terok_clearance_hub.te")),
    Path(str(_RES / "selinux/terok_clearance_notifier.te")),
)
"""SELinux ``.te`` source files for the hub + notifier confined
domains.  Verdict daemon stays unconfined — see the ``.te`` header
for the rationale (it execs ``terok-shield allow|deny`` which
shells through ``podman unshare nsenter nft`` — that exec chain
doesn't tolerate tight labelling)."""

PERMISSIVE_DOMAINS: tuple[str, ...] = (
    "terok_clearance_hub_t",
    "terok_clearance_notifier_t",
)
"""Marked permissive at install time for the soak window."""

APPARMOR_PROFILES: tuple[Path, ...] = tuple(
    Path(str(_RES / f"apparmor/{p}")) for p in ("terok-clearance-hub", "terok-clearance-notifier")
)
"""Empty in practice today — clearance ships no AppArmor profiles
yet (the apparmor/ subdir doesn't exist in clearance's resources).
Kept as a list so the ``install`` body matches the sandbox one
shape-for-shape; iterating an empty tuple is a clean no-op when the
files aren't there."""

SERVICE_UNITS: tuple[tuple[str, str, str], ...] = (
    ("terok-clearance-hub.service", "terok_clearance_hub_t", "terok-clearance-hub"),
    (
        "terok-clearance-notifier.service",
        "terok_clearance_notifier_t",
        "terok-clearance-notifier",
    ),
)
"""Per-unit drop-in metadata.  Verdict service intentionally absent
— see ``terok_clearance_notifier.te`` header for why it stays
unconfined."""

INSTALL_COMMAND = "python -m terok_clearance.tools.hardening install"
"""Single source for the install invocation string this package's
own status reporter renders.  The terok package's
``tools/hardening.py`` orchestrator chains this with the matching
sandbox tool when both are installed."""


_SELINUX_ENFORCE = Path("/sys/fs/selinux/enforce")
_APPARMOR_ROOT = Path("/sys/kernel/security/apparmor")


# ---------- Install ----------


def install() -> None:
    """Load modules, write drop-ins, restart active units.

    Same sequence + idempotence guarantees as
    `terok_sandbox.hardening.install`.  Caller (the ``terok
    hardening install`` orchestrator) caches sudo credentials up
    front so the user enters their password once total.
    """
    if _SELINUX_ENFORCE.is_file():
        for tool in ("checkmodule", "semodule_package", "semodule", "semanage"):
            if not shutil.which(tool):
                sys.exit(
                    f"error: {tool} not found "
                    "(install: dnf install selinux-policy-devel "
                    "policycoreutils-python-utils)"
                )
        print("==> clearance: loading SELinux modules")
        with tempfile.TemporaryDirectory(prefix="terok-clearance-") as wd:
            for te in SELINUX_MODULES:
                mod = Path(wd) / f"{te.stem}.mod"
                pp = Path(wd) / f"{te.stem}.pp"
                subprocess.run(["checkmodule", "-M", "-m", "-o", str(mod), str(te)], check=True)
                subprocess.run(["semodule_package", "-o", str(pp), "-m", str(mod)], check=True)
                subprocess.run(["sudo", "semodule", "-i", str(pp)], check=True)
                print(f"    loaded {te.stem}")
        for dom in PERMISSIVE_DOMAINS:
            subprocess.run(
                ["sudo", "semanage", "permissive", "-a", dom],
                check=False,
                capture_output=True,
            )
            print(f"    {dom} permissive (soak)")

    if _APPARMOR_ROOT.is_dir() and shutil.which("apparmor_parser"):
        installed = [p for p in APPARMOR_PROFILES if p.exists()]
        if installed:
            print("==> clearance: loading AppArmor profiles")
            for p in installed:
                target = f"/etc/apparmor.d/{p.name}"
                subprocess.run(["sudo", "install", "-m", "0644", str(p), target], check=True)
                subprocess.run(["sudo", "apparmor_parser", "-r", target], check=True)
                print(f"    loaded {p.name}")

    _write_dropins()
    _restart_active_units()


# ---------- Remove ----------


def remove() -> None:
    """Tear down everything `install` set up; reverse install order."""
    _remove_dropins()
    _restart_active_units()

    if _APPARMOR_ROOT.is_dir() and shutil.which("apparmor_parser"):
        for p in APPARMOR_PROFILES:
            target = Path(f"/etc/apparmor.d/{p.name}")
            if target.exists():
                subprocess.run(["sudo", "apparmor_parser", "-R", str(target)], check=False)
                subprocess.run(["sudo", "rm", "-f", str(target)], check=False)
                print(f"    unloaded {p.name}")

    if _SELINUX_ENFORCE.is_file() and shutil.which("semodule"):
        print("==> clearance: unloading SELinux modules")
        for dom in PERMISSIVE_DOMAINS:
            subprocess.run(
                ["sudo", "semanage", "permissive", "-d", dom],
                check=False,
                capture_output=True,
            )
        for te in SELINUX_MODULES:
            subprocess.run(
                ["sudo", "semodule", "-r", te.stem],
                check=False,
                capture_output=True,
            )
            print(f"    unloaded {te.stem}")


# ---------- Drop-in helpers ----------


def _write_dropins() -> None:
    selinux = _SELINUX_ENFORCE.is_file()
    apparmor = _APPARMOR_ROOT.is_dir()
    if not (selinux or apparmor):
        return
    unit_dir = Path.home() / ".config/systemd/user"
    if not unit_dir.is_dir():
        sys.exit(
            f"error: {unit_dir} missing — run `terok setup` as the same "
            "user, then re-run hardening install"
        )
    print("==> clearance: writing systemd drop-ins")
    for unit, sel_type, aa_profile in SERVICE_UNITS:
        body: list[str] = []
        if selinux:
            body.append(f"SELinuxContext=-unconfined_u:unconfined_r:{sel_type}:s0")
        if apparmor and Path(f"/etc/apparmor.d/{aa_profile}").exists():
            body.append(f"AppArmorProfile={aa_profile}")
        if not body:
            continue
        d = unit_dir / f"{unit}.d"
        d.mkdir(parents=True, exist_ok=True)
        f = d / "hardening-mac.conf"
        f.write_text(
            "# Installed by `terok hardening install`\n[Service]\n" + "\n".join(body) + "\n"
        )
        print(f"    wrote {f.relative_to(unit_dir)}")


def _remove_dropins() -> None:
    unit_dir = Path.home() / ".config/systemd/user"
    if not unit_dir.is_dir():
        return
    print("==> clearance: removing systemd drop-ins")
    for unit, _, _ in SERVICE_UNITS:
        f = unit_dir / f"{unit}.d" / "hardening-mac.conf"
        if not f.exists():
            continue
        f.unlink()
        try:
            f.parent.rmdir()
        except OSError:
            pass
        print(f"    removed {f.relative_to(unit_dir)}")


def _restart_active_units() -> None:
    print("==> clearance: restarting active units")
    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    for unit, _, _ in SERVICE_UNITS:
        active = subprocess.run(["systemctl", "--user", "is-active", "--quiet", unit])
        if active.returncode == 0:
            subprocess.run(["systemctl", "--user", "restart", unit], check=False)
            print(f"    restarted {unit}")


# ---------- Status (libselinux + AppArmor probes) ----------


CONFINED_DOMAINS: tuple[str, ...] = (
    "terok_clearance_hub_t",
    "terok_clearance_notifier_t",
)
"""SELinux process domains shipped by `SELINUX_MODULES`.  Mirrors the
``.te`` declarations; kept as a tuple here so `status` can probe
each via ``security_check_context()`` without re-parsing the
modules."""

CONFINED_PROFILES: tuple[str, ...] = (
    "terok-clearance-hub",
    "terok-clearance-notifier",
)
"""AppArmor profile names shipped by `APPARMOR_PROFILES`."""


def _is_domain_loaded(domain: str) -> bool:
    """Probe libselinux to check whether *domain* is in the loaded policy."""
    import ctypes

    try:
        lib = ctypes.CDLL("libselinux.so.1", use_errno=True)
    except OSError:
        return False
    lib.security_check_context.argtypes = [ctypes.c_char_p]
    lib.security_check_context.restype = ctypes.c_int
    ctx = f"system_u:system_r:{domain}:s0".encode()
    return lib.security_check_context(ctx) == 0


def _loaded_apparmor_profiles() -> set[str]:
    """Parse ``/sys/kernel/security/apparmor/profiles`` into a name set."""
    f = _APPARMOR_ROOT / "profiles"
    if not f.is_file():
        return set()
    try:
        text = f.read_text()
    except (PermissionError, OSError):
        return set()
    out: set[str] = set()
    for line in text.splitlines():
        if not line.endswith(")"):
            continue
        head, _, _ = line.rpartition(" (")
        if head:
            out.add(head.strip())
    return out


def status() -> None:
    """Print whether clearance hardening is currently loaded."""
    sel: tuple[str, ...] = ()
    if _SELINUX_ENFORCE.is_file():
        sel = tuple(d for d in CONFINED_DOMAINS if _is_domain_loaded(d))
    aa: tuple[str, ...] = ()
    if _APPARMOR_ROOT.is_dir():
        loaded = _loaded_apparmor_profiles()
        aa = tuple(p for p in CONFINED_PROFILES if p in loaded)
    print(f"SELinux domains:   {', '.join(sel) if sel else '(none loaded)'}")
    print(f"AppArmor profiles: {', '.join(aa) if aa else '(none loaded)'}")


# ---------- Entrypoint ----------


def main() -> None:
    """Argparse dispatcher for ``python -m terok_clearance.tools.hardening``."""
    import argparse

    p = argparse.ArgumentParser(
        prog="python -m terok_clearance.tools.hardening",
        description="Optional MAC hardening for terok-clearance (hub + notifier).",
    )
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("install", help="Load modules + write systemd drop-ins")
    sub.add_parser("remove", help="Tear down modules + drop-ins")
    sub.add_parser("status", help="Print loaded domains / profiles")
    args = p.parse_args()
    {"install": install, "remove": remove, "status": status}[args.cmd]()


if __name__ == "__main__":  # pragma: no cover
    main()
