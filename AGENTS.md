# Agent Guide (terok-clearance)

## Purpose

`terok-clearance` provides D-Bus desktop notifications for the terok clearance system. It wraps the freedesktop Notifications spec via `dbus-fast`, exposing an async-first API with action buttons and a graceful no-op fallback for headless environments.

## Technology Stack

- **Language**: Python 3.12+
- **Package Manager**: uv
- **Testing**: pytest + pytest-asyncio with coverage
- **Linting/Formatting**: ruff
- **Module Boundaries**: tach (enforced in CI via `tach.toml`)
- **Security**: bandit (SAST)

## Repo layout

- `src/terok_clearance/`: Python package.  Public API is curated in `__init__.py`; internals live in feature-grouped sub-packages (`domain/`, `wire/`, `hub/`, `client/`, `notifications/`, `runtime/`, `cli/`) with `tach` layers running orthogonal to the directory tree.
- `tests/`: pytest test suite
- `docs/`: MkDocs documentation source

## Build, Lint, and Test Commands

**During development — ALWAYS use the fast loop:**
```bash
make test-fast # Only the tests affected by your branch diff (tach impact analysis)
```
Rerunning the full suite after every edit is the single biggest time sink in
agent dev loops — don't do it. Iterate with `make test-fast`; run the full
`make test` exactly once, right before committing. One exception: impact
analysis follows the Python import graph only, so after changing non-Python
inputs (resource YAML, templates, shell scripts) `make test-fast` skips tests
that are actually affected — run the full `make test` for those changes.

**Before committing:**
```bash
make lint      # Run linter (required before every commit)
make format    # Auto-fix lint issues if lint fails
make test      # Full unit suite — once, after iterating with test-fast
```

**Before pushing:**
```bash
make test-unit   # Run unit tests with coverage
make tach        # Check module boundary rules (tach.toml)
make docstrings  # Check docstring coverage (minimum 95%)
make reuse       # Check REUSE (SPDX license/copyright) compliance
make check       # Run lint + test-unit + tach + security + docstrings + deadcode + reuse
```

**Other useful commands:**
```bash
make install-dev  # Install all development dependencies
make security     # Run bandit SAST scan
make clean        # Remove build artifacts
make spdx NAME="Real Human Name" FILES="src/terok_clearance/foo.py"  # Add SPDX header
```

## Coding Standards

- **Style**: Follow ruff configuration in `pyproject.toml`
- **Line length**: 100 characters (ruff formatter target; `E501` is disabled so long strings that cannot be auto-wrapped are tolerated)
- **Imports**: Sorted with isort (part of ruff)
- **Type hints**: Use Python 3.12+ type hints (`X | None`, not `Optional[X]`)
- **Docstrings**: Required for all public functions, classes, and modules (enforced by `docstr-coverage` at 95% minimum in CI)
- **Cross-references in docstrings**: use mkdocstrings autoref syntax `` [`Name`][module.path.Name] `` — never the Sphinx ``:class:`Name``` / ``:func:`name``` forms. Sphinx roles render as literal text on the rendered docs site (mkdocstrings doesn't process them). Prefer the explicit full path over the bare `` [`Name`][] `` autoref form: explicit paths keep `properdocs build --strict` green even when the symbol's short name isn't unique. For external symbols, use the dependency's own path (e.g. `` [`Sandbox`][terok_sandbox.Sandbox] ``, `` [`StreamReader`][asyncio.StreamReader] ``) — those resolve via the inventories listed in `properdocs.yml`.
- **Pythonic style**: Prefer modern Pythonic constructs (comprehensions, ternary expressions, walrus operator, unpacking) where they improve readability
- **Testing**: Add tests for new functionality; maintain coverage
- **SPDX headers**: Every source file (`.py`) must have an SPDX header. Use `make spdx` to add or update it:
  ```bash
  make spdx NAME="Real Human Name" FILES="path/to/file.py"
  ```
  - **New file** → creates the header:
    ```python
    # SPDX-FileCopyrightText: 2026 Jiri Vyskocil
    # SPDX-License-Identifier: Apache-2.0
    ```
  - **Existing file** → adds an additional copyright line (preserves the original)
  When modifying an existing file, always run `make spdx` with the contributor's name. NAME must be a real person's name (ASCII-only), not a project name. Use a single year (year of first contribution), not a range. Files covered by `REUSE.toml` glob patterns (`.md`, `.yml`, `.toml`, `.json`, etc.) do not need inline headers.
- **Documentation filenames**: Markdown files under `docs/` use `lowercase.md` naming (e.g. `developer.md`). Root-level project files (`README.md`, `AGENTS.md`) stay UPPERCASE per standard convention.
- **Public API surface**: `__init__.py` + `__all__` is the contract. Symbols listed in `__all__` are stable across minor releases; anything underscore-prefixed or absent from `__all__` is internal and may change without notice. Review the list before each release — stable APIs stay small because growing them costs.

## Module Boundaries (tach)

The project uses [tach](https://github.com/gauge-sh/tach) to enforce module boundary rules defined in `tach.toml`. When adding new cross-module imports:

- Check `tach.toml` for allowed dependencies
- Run `make tach` to verify
- If adding a new dependency between modules, update `depends_on` in `tach.toml`
- CI will reject boundary violations

Architecture is two-axis: feature-grouped directories + orthogonal
tach layers.  Directories say *what* (clearance flow, wire schema,
deployment plumbing); layers say *how free* (domain modules know
nothing about wire; wire knows only domain; infrastructure knows
both; interface knows all of the above).

```text
interface       ──→ cli/, __init__.py
infrastructure  ──→ hub/, client/, notifications/{desktop,null,callback,factory}, runtime/
wire            ──→ wire/
domain          ──→ domain/, notifications/protocol.py
```

A leaf may import from any layer below its own without listing the
dep; same-layer / cross-feature deps must be explicit in
``tach.toml``'s ``[[modules]] depends_on``.

## Development Workflow

1. Make changes in `src/terok_clearance/`
2. Run `make lint` frequently during development
3. Add/update tests in `tests/`
4. Run `make test-unit` to verify changes
5. If you added or changed cross-module imports, run `make tach` to verify module boundary rules
6. Run `make check` before pushing

## Key Guidelines

- **Async-only**: No sync wrappers; consumers own the sync→async bridge
- **Graceful fallback**: `create_notifier()` returns `NullNotifier` when D-Bus is unavailable
- **Minimal changes**: Make surgical, focused changes
- **Existing tests**: Never remove or modify unrelated tests
- **Dependencies**: Use uv; runtime dependencies are `dbus-fast`, `asyncvarlink`, `pyyaml`, and `terok-util`
- **Pre-1.0 external deps**: pin third-party `0.x` dependencies to an exact patch (`asyncvarlink==0.3.2`), not a `<0.y+1` range. Pre-1.0 projects routinely ship breaking changes in patch/minor bumps, so a floating range lets a fresh install pull an incompatible release. Bump the pin deliberately (and re-run the suite) rather than letting it float. This is a terok-stack-wide practice; our own `terok-*` siblings are exempt (we control their API and range-pin them per the stack's version-sync rules).

## Dependency Pinning & `pyproject.toml` Hygiene

**Version pinning policy.** Runtime/production dependencies — those pulled in
by a plain `pip install` / `pipx install` of this package (the
`[project].dependencies` table) — are pinned by the dependency's major
version:

- **Third-party, major 0 (`0.y.z`)** → pin to an **exact patch**
  (`pkg==0.y.z`). Pre-1.0 packages promise no compatibility across either
  minors *or* patches, so a floating range invites silent breakage.
- **Third-party, major ≥ 1** → **compatible-release at the tested
  baseline**: `pkg~=X.Y` where `X.Y` is the locked major.minor (floor =
  what we test against, cap = next major). Use the patch-series form
  `pkg~=X.Y.Z` only where a specific patch floor is required — note the
  PEP 440 truncation rule: the cap is one level above the last written
  component (`~=2.13` → `<3`, `~=8.2.5` → `<8.3`). Prefer `~=` over a
  hand-rolled `>=,<` pair: it states the baseline as one fact with the
  ceiling derived by construction, so the bounds cannot drift apart.
- **Sibling `terok-*` deps** → `~=0.y.z` (or their release-wheel URL pin).
  We guarantee patch-level API stability across the sibling packages, so
  the patch-series form is exactly right — do *not* exact-pin them (it
  would fight the multi-repo release/PR-chain flow).

Dev / test / docs / tooling dependencies (the `[dependency-groups]` tables)
are **exempt** — they are not shipped to installers and exact-pinning them is
an unwarranted maintenance burden the developers can absorb. After changing
any pin, run `uv lock` and commit `pyproject.toml` and `uv.lock`
together.

**Comment discipline in `pyproject.toml`.** The dependency tables stay
comment-free and self-documenting, apart from the standing policy pointer
above them. **Never** comment on why a dependency -- especially a sibling
`terok-*` package -- is pinned a certain way, and never mention dev-cycle
state (temporary git-branch pins, the multi-repo PR chain): cross-repo
merges are performed by a script that does not understand comments, so any
such note is carried straight into a production release. Keep pin
rationale in commit messages, PR descriptions, or this file. Ordinary
explanatory comments in `[tool.*]` sections are fine. `pyproject.toml`
stays ASCII-only.
