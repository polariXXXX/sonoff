# Repository Guidelines

## Scope & Architecture

Build a Python service and web application controlling Sonoff sockets through eWeLink on Raspberry Pi Zero 2 W (512 MB RAM). Prefer one asynchronous Python process with FastAPI, a background sensor monitor, independent socket automation, and SQLite. Package deployment with Docker Compose. Read `docs/architecture.md` for proposed behavior and unresolved decisions. The initial implementation exists; hardware/live-cloud and public certificate issuance require deployment validation.

## Project Structure

Place application modules in `src/sonoff/`, tests in `tests/`, and templates/static assets within the application package. Keep non-secret configuration examples in `config/`, design documentation in `docs/`, and repository skills in `.agents/skills/`. Read `.agents/skills/sonoff-development/SKILL.md` when working on automation, GPIO, or eWeLink integration.

## Development & Validation

Target Python 3.12 on Raspberry Pi OS Lite 64-bit. Use `uv sync --extra dev --locked`, `uv run pytest -q`, `uv run ruff check src tests scripts`, and `uv run ruff format --check src tests scripts`. Production ARM64/GPIO dependencies are pinned in `requirements.lock`. Run `docker compose up --build -d app` for simulation; add `--profile https` for Caddy. See `README.md` for secrets, domain, and GPIO setup.

## Coding & Testing

Use four-space indentation, type annotations, `snake_case` functions/modules, and `PascalCase` classes. Separate sensor, automation, persistence, web, and cloud adapters. Inject clocks and mock adapters; tests must not require GPIO or live sockets. Test debounce, cancelled timers, independent delays, restart recovery, connectivity loss, token expiry, and manual-control precedence. Name tests `test_<behavior>` and avoid real sleeps.

## Hardware & Automation

Pi GPIO accepts 3.3 V digital inputs and has no analog input. Never connect mains or 5 V directly. Confirm analyzer isolation and output before final wiring. Revalidate pending actions after sensor changes and before retries; never replay stale ON commands. Distinguish desired, confirmed, offline, and unknown device states. Run exactly one automation worker. Boiler defaults are OFF immediately and ON after 20 stable minutes; startup restarts the full delay. LOW means mains present. Manual ON/OFF override sensor automation until automatic mode is restored; unknown mains blocks only automatic ON.

## Security & Contributions

`../.env` contains `EWL_APPID` and `EWL_SECRET`, which are app credentials, not account access tokens. Never print or commit secrets, OAuth tokens, or authenticated remote URLs. Load secrets at runtime outside images. Use focused imperative commits. PRs should describe behavior, configuration changes, and validation. The user authorized live ON/OFF testing only on Skvyra (`10025391ef`); restore its initial state. Other live switching needs explicit instruction. Ignore `token*.json`; import `../tokens.json` at runtime only.
