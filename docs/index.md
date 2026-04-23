# terok-clearance

D-Bus desktop notification package for the terok clearance system.

## What it does

terok-clearance wraps the [freedesktop Notifications](https://specifications.freedesktop.org/notification-spec/latest/) D-Bus interface via [`dbus-fast`](https://github.com/Bluetooth-Devices/dbus-fast), providing an async-first Python API for desktop notifications with action buttons.

### Key properties

- **Async-first** — built on `dbus-fast` with native asyncio support
- **Action buttons** — notifications can carry interactive actions (Allow / Deny)
- **Signal handling** — listen for `ActionInvoked` and `NotificationClosed` signals
- **Graceful fallback** — `create_notifier()` returns a silent `NullNotifier` when D-Bus is unavailable (headless, container, CI)
- **Protocol-based** — consumers type-hint against `Notifier` (PEP 544 Protocol)

## Quick start

### Install

```bash
pip install terok-clearance
```

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

## API preview

| Symbol | Description |
|--------|-------------|
| `create_notifier()` | Async factory — returns `DesktopNotifier` or `NullNotifier` |
| `DesktopNotifier` | Real D-Bus client via `dbus-fast` |
| `NullNotifier` | No-op fallback (all methods return immediately) |
| `Notifier` | PEP 544 Protocol for consumer type hints |

## Next steps

- [Contributing](developer.md) — development setup and conventions
- [API Reference](reference/) — full module documentation
