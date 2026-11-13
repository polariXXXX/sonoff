---
name: sonoff-development
description: Develop this repository's Python Sonoff automation and web application for Raspberry Pi Zero 2 W, including eWeLink integration, GPIO monitoring, per-socket flows, and Docker deployment.
---

# Sonoff Development

Read `AGENTS.md` and `docs/architecture.md` before changing hardware or automation. Proposed wiring and illustrative delays are not approved production defaults.

## Hardware and runtime

Target Zero 2 W, 512 MB RAM, Raspberry Pi OS Lite 64-bit and Python 3.12. Use one asyncio process and exactly one automation worker. Pi GPIO is digital, 3.3 V only. Confirm analyzer isolation and output before final wiring; analog signals need an external ADC. Distinguish BCM from physical pin numbers.

## Automation

Give every socket independent rules and timers. Debounce inputs; use monotonic time for delays. Invalidate pending actions on signal reversal, rule edits or manual-mode changes. Before sending or retrying, recheck signal, mode and action generation. Serialize commands per socket; reconcile cloud responses against the current generation without holding state locks during network I/O. Restart must sample the sensor again and follow the approved startup policy rather than replay old ON commands. Unknown states and manual ON during mains absence require explicit behavior.

## Cloud integration

Consult [official OAuth and API documentation](https://coolkit-technologies.github.io/eWeLink-API/#/en/OAuth2.0). `EWL_APPID` and `EWL_SECRET` in `../.env` alone do not authorize user devices. Validate OAuth state and registered redirect URL; derive host from account region. Store rotating tokens atomically in protected runtime storage and redact logs. Discover UIID/channel capabilities before forming payloads. Send explicit ON/OFF, never toggle. Prefer WebSocket events and infrequent HTTP reconciliation within app quotas. API acknowledgement differs from confirmed device state. Bound timeouts/retries and discard obsolete commands.

## Validation and deployment

Use mock GPIO/cloud adapters and injected clocks; tests never switch live sockets. Docker loads secrets at runtime and mounts only the GPIO device needed without privileged mode. Verify ARM64 dependencies, volume permissions, graceful shutdown and single-worker GPIO access on real Raspberry before claiming readiness.

## Approved behavior and deployment

Boiler default: OFF immediately when mains disappears, ON after 20 uninterrupted minutes of mains presence (LOW). Restart and rule edits start a new full delay. OFF-on-absence also applies in manual mode; manual ON requires confirmed presence. Preserve these semantics unless the user changes them.

The detector listing mixes TTL 3–5 V and 24 V PLC outputs: direct GPIO wiring is unconfirmed. Use GPIO17/physical pin 11 only after confirming 0–3.3 V output or suitable level conversion. Open-collector pull-up to 3.3 V requires confirmation that no 5/24 V pull-up remains.

The user authorized live switching of Skvyra (`10025391ef`) for testing. Restore its initial confirmed state; this does not authorize other devices. `scripts/test_skvyra.py` reads `../.env` and `../tokens.json` without printing secrets. Never commit devicekeys or token JSON. Token import is initial only; runtime rotations persist transactionally in SQLite.

Basic Auth is required. Caddy provides Let's Encrypt via configured domain/email and reachable 80/443; DNS challenge is not yet configured. Containers use restart-always; `deploy/sonoff-controller.service` enables Compose startup through systemd. Hardware installation, public DNS, and certificate issuance still require the actual deployment environment.
