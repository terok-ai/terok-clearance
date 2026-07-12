# Contributing

## Development setup

```bash
git clone git@github.com:terok-ai/terok-clearance.git
cd terok-clearance
make install-dev
```

### System dependencies

uv pulls in `dbus-python` (used by `python-dbusmock` in the test
suite for a private session bus).  It has no wheel and builds from
source against the system D-Bus and GLib development headers, so
those need to be installed before `make install-dev` — otherwise the
build fails with `dbus/dbus.h: No such file or directory`.

```bash
# Fedora / RHEL
sudo dnf install dbus-devel glib2-devel python-devel gcc

# Debian / Ubuntu (adjust the python3.X-dev version to match your interpreter)
sudo apt install libdbus-1-dev libglib2.0-dev python3.12-dev gcc
```

The test matrix containers install these automatically — see
``tests/containers/Containerfile.*``.

## Commands

```bash
# Before every commit
make lint             # ruff check + format check
make format           # auto-fix lint issues

# Before pushing
make test-unit        # unit tests with coverage
make check            # core local suite (lint + test-unit + tach + typecheck + security + docstrings + deadcode + reuse)

# Other
make tach             # check module boundary rules
make typecheck        # mypy static type check
make security         # bandit SAST scan
make docstrings       # docstring coverage (95% minimum)
make reuse            # SPDX license compliance
make docs             # serve documentation locally
```

## Conventions

- **Python 3.12+** with modern type hints (`X | None`, not `Optional[X]`)
- **ruff** for linting and formatting (100 char line length)
- **SPDX headers** on all `.py` files — use `make spdx NAME="Real Human Name" FILES="path"`
- **Docstrings** on all public functions (95% coverage enforced in CI)
- **Module boundaries** enforced by tach (`tach.toml`) — run `make tach` after changing imports
- **Documentation filenames** under `docs/` use `lowercase.md` (e.g. `developer.md`) to match MkDocs convention; root-level files (`README.md`, `AGENTS.md`) stay UPPERCASE

## Testing

### Unit tests

```bash
make test-unit    # runs tests/ with coverage
```

The current test suite does not require a desktop session or notification
daemon. Generated reports go under `reports/`.

## Architecture

### Module structure

```text
domain/             — ClearanceEvent, VerdictAction, Dossier (pure types)
wire/               — varlink interface definitions, typed errors, socket helpers
hub/                — ClearanceHub varlink server + event ingester
verdict/            — VerdictServer / VerdictClient (applies verdicts via `terok-shield allow|deny`)
client/             — ClearanceClient (varlink RPC), EventSubscriber, MultiSocketSubscriber
notifications/      — Notifier protocol, D-Bus notifier, null fallback, create_notifier()
runtime/            — shared serve() helpers for the standalone entry points
cli/ + commands.py  — the `terok-clearance-hub` CLI registry
__init__            — public API re-exports
```

### Dependency rules

Module boundaries are enforced by tach — see the layer assignments in
`tach.toml`.  The public API surface is the `__init__` re-export list.
