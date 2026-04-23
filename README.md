# terok-clearance

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache_2.0-blue.svg)](https://opensource.org/licenses/Apache-2.0)
[![REUSE status](https://api.reuse.software/badge/github.com/terok-ai/terok-clearance)](https://api.reuse.software/info/github.com/terok-ai/terok-clearance)
[![Quality Gate Status](https://sonarcloud.io/api/project_badges/measure?project=terok-ai_terok-clearance&metric=alert_status)](https://sonarcloud.io/summary/new_code?id=terok-ai_terok-clearance)

D-Bus desktop notification package for the terok clearance system.

## Overview

terok-clearance wraps the [freedesktop Notifications](https://specifications.freedesktop.org/notification-spec/latest/) D-Bus interface via [`dbus-fast`](https://github.com/Bluetooth-Devices/dbus-fast), providing an async-first Python API for desktop notifications with action buttons and a graceful fallback for headless environments.

### Features

- **Async-first** — native asyncio via `dbus-fast`
- **Action buttons** — notifications with interactive actions (Allow / Deny)
- **Signal handling** — `ActionInvoked` and `NotificationClosed` callbacks
- **Graceful fallback** — `NullNotifier` when D-Bus is unavailable
- **Protocol-based** — consumers type-hint against `Notifier` (PEP 544)

### Requirements

- Linux with a D-Bus session bus (any desktop environment with a notification daemon)
- Python 3.12+

## Installation

```bash
pip install terok-clearance
```

## Quick start

### Send a notification

```python
import asyncio
from terok_clearance import create_notifier

async def main():
    notifier = await create_notifier(app_name="terok")
    action_received = asyncio.Event()

    def on_action(nid, key):
        print(f"{nid}: {key}")
        action_received.set()

    notifier.on_action(on_action)

    nid = await notifier.notify(
        "Clearance request",
        "Task alpha wants access to api.github.com:443",
        actions={"allow": "Allow", "deny": "Deny"},
    )

    await action_received.wait()
    await notifier.close()

asyncio.run(main())
```

### CLI tool (development / testing)

```bash
terok-clearance-notify "Title" "Body" --actions allow:Allow deny:Deny --wait
```

## Documentation

- **[User Guide](https://terok-ai.github.io/terok-clearance/)** — overview, quick start, API preview
- **[Developer Guide](https://terok-ai.github.io/terok-clearance/developer/)** — contributing, conventions, architecture

## License

Apache-2.0 — see [LICENSES/Apache-2.0.txt](LICENSES/Apache-2.0.txt).
