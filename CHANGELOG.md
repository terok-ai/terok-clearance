# Changelog

## v0.8.3 — Twenty-Six Hours

## What's Changed
* feat: publish the schema of the clearance config section in https://github.com/terok-ai/terok-clearance/pull/282

**Full Changelog**: https://github.com/terok-ai/terok-clearance/compare/v0.8.2...v0.8.3

## v0.8.2 — A Man Alone

- Mute the Allow/Deny prompts of one container: `SetMute(container, muted)` mutes or unmutes
  it, and `Mutes()` returns the muted containers. The hub drops the blocked-connection events
  of a muted container before it fans them out, so no request waits for an answer that
  nobody can give. The shield still refuses each connection, and records it while audit is
  on. A mute can also be given when the hub starts, so no prompt appears before it applies ([#267](https://github.com/terok-ai/terok-clearance/pull/267)).
- The hub keys each container by its 12-character short id, the id that the NFLOG reader
  sends. A host-side `shield_down` that names the podman container now clears its pending
  verdicts ([#267](https://github.com/terok-ai/terok-clearance/pull/267)).
- A pending verdict expires. `clearance.verdict_ttl` in `config.yml` sets the time; the
  default is 24 hours, and `never` turns the expiry off. Before, an unanswered entry stayed
  until the container ended, and the hub grew ([#256](https://github.com/terok-ai/terok-clearance/pull/256)).
- Python 3.15 is supported.

**Full Changelog**: https://github.com/terok-ai/terok-clearance/compare/v0.8.1...v0.8.2

## v0.8.1 — At Your Service

- Use shared host-PATH lookup while preserving installation-bound Shield companions.

## What's Changed
* fix: align host tool discovery by @sliwowitz in https://github.com/terok-ai/terok-clearance/pull/248

**Full Changelog**: https://github.com/terok-ai/terok-clearance/compare/v0.8.0...v0.8.1

## v0.8.0 — Past Prologue

## What's Changed
* Route logging through terok-util unified facility by @sliwowitz in https://github.com/terok-ai/terok-clearance/pull/214
* Add the Manjaro (AppArmor) matrix slot by @sliwowitz in https://github.com/terok-ai/terok-clearance/pull/232


**Full Changelog**: https://github.com/terok-ai/terok-clearance/compare/v0.7.3...v0.8.0

## v0.7.3 — The Celestial Temple

* In-namespace bus auth; non-systemd + Mageia slots, https://github.com/terok-ai/terok-clearance/pull/173
* The CLI identifies itself by its installed name, https://github.com/terok-ai/terok-clearance/pull/178

**Full Changelog**: https://github.com/terok-ai/terok-clearance/compare/v0.7.2...v0.7.3

## v0.7.2 — asyncvarlink hotfix

Fixes compatibility with asyncvarlink 0.3.2, https://github.com/terok-ai/terok-clearance/pull/171

**Full Changelog**: https://github.com/terok-ai/terok-clearance/compare/v0.7.1...v0.7.2

## v0.7.1 — Emissary, Part II

Added PyPI version badge and logo, https://github.com/terok-ai/terok-clearance/pull/156, homepage URL, terok cross-link, and minor docs update https://github.com/terok-ai/terok-clearance/pull/157, and updated dependencies.

**Full Changelog**: https://github.com/terok-ai/terok-clearance/compare/v0.7.0...v0.7.1

## v0.7.0 — The Emissary

**First public PyPi release**

## Notable changes

* move command registry to top-level terok_clearance.commands in https://github.com/terok-ai/terok-clearance/pull/121
* theme-aware app icon — system icon-theme first, bundled SVG fallback in https://github.com/terok-ai/terok-clearance/pull/128
* per-container supervisor model in https://github.com/terok-ai/terok-clearance/pull/151

**Full Changelog**: https://github.com/terok-ai/terok-clearance/compare/v0.6.13...v0.7.0

