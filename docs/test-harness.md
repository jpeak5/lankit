# Test Harness

## Background

lankit has no automated tests today. The `tests/` directory is empty. The only test
infrastructure that exists is `lankit test-failsafe` — a manual CLI command that verifies
the RouterOS scheduler-based auto-revert fires correctly on a live router. `TESTING.md`
documents a 5-phase manual commissioning runbook for real hardware.

This document describes the plan for a hardware-in-the-loop test harness using a
dedicated lab router and Pi, plus an offline unit/integration test suite built around
a mock router connection.

## Hardware

- **Lab router**: older MikroTik model (not the production hAP ax³)
- **Lab Pi**: spare Raspberry Pi running Raspberry Pi OS
- **Isolation**: lab router runs standalone, physically separate from the production
  network — not connected to the same switch

The older router model is valuable specifically because it may have RouterOS quirks the
hAP ax³ doesn't. Differences in `/export` output format or clock output will surface
portability bugs (e.g. the clock-parsing failsafe gap) before users encounter them.

## Configuration isolation

The lab gets its own config file, never shared with production:

```
lab-network.yml          # lab-specific IPs, SSIDs, hostnames
```

All lankit commands targeting the lab use:

```bash
export LANKIT_CONFIG=lab-network.yml
```

This prevents accidentally targeting the production router.

## Test layers

### Layer 1 — Offline unit tests (no hardware)

Tests that exercise config loading, validation, and script generation without any
SSH connection.

Covers:
- `lankit/core/config.py` — load and validate `network.yml`, detect CHOOSE markers,
  schema enforcement
- `lankit/core/generator.py` — Jinja2 rendering produces correct `.rsc` output for
  known inputs
- `lankit/core/snapshots.py` — snapshot save/load/rotate/index
- CLI argument parsing and pre-flight guards (e.g. `lankit rules` with empty
  `ansible/generated/`)

No mocking required — these are pure-Python operations.

### Layer 2 — Integration tests using MockRouterConnection

Tests that exercise the full command flow (apply, audit, probe, rollback) using a
fake router that returns captured RouterOS output instead of making SSH calls.

#### MockRouterConnection design

`RouterConnection` in `lankit/core/router.py` is a context manager with 8 methods:

```python
run(command) -> str
run_tolerant(command) -> tuple[str, str]
upload(content, remote_path) -> None
add_failsafe_scheduler(name, revert_cmd, seconds) -> None
cancel_failsafe_scheduler(name) -> None
export_config() -> str
identity() -> str
version() -> str
```

The plan is a `MockRouterConnection` subclass (or protocol implementation) that:
- Accepts a fixture dict mapping command strings to (stdout, stderr) response pairs
- Records all commands called (for assertion: "did apply upload the right scripts?")
- Raises `RouterError` on demand (to test error paths)
- Returns realistic RouterOS output from captured fixtures

#### Fixtures

RouterOS output is captured from the lab router into YAML fixture files:

```
tests/fixtures/
  export_verbose.txt       # output of /export verbose on a provisioned router
  dhcp_leases.txt          # output of /ip dhcp-server lease print
  interface_vlan.txt       # output of /interface vlan print
  firewall_filter.txt      # output of /ip firewall filter print
  identity.txt             # output of /system identity print
  version.txt              # output of /system resource print
```

Multiple fixture sets cover different router states:
- `fixtures/clean/` — factory-reset router with no lankit config
- `fixtures/provisioned/` — fully provisioned router (all 8 segments)
- `fixtures/drifted/` — router with one manually-added rogue rule

#### Dependency injection

Commands that currently hard-wire `RouterConnection(ip, user, key)` will accept an
optional `connection` parameter:

```python
def run_apply(config, connection=None):
    if connection is None:
        connection = RouterConnection(config.router.ip, ...)
    with connection as conn:
        ...
```

Tests pass in a `MockRouterConnection`. Production code path is unchanged.

### Layer 3 — Hardware integration tests (lab router required)

End-to-end tests that run against the real lab router. These are slower and require
the lab to be powered on and reachable, but they catch things the mock cannot:

- Actual RouterOS `/import` behavior (script syntax errors, idempotency)
- Failsafe timer fires at the correct time (timing-dependent)
- SFTP upload succeeds and files land in `/tmp/` correctly
- `lankit provision` runs Ansible against the lab Pi and services come up

These tests are marked `@pytest.mark.hardware` and skipped by default:

```bash
pytest                          # runs layers 1 + 2 only
pytest -m hardware              # runs layer 3 (requires lab hardware)
```

The existing `lankit test-failsafe` command remains as the manual commissioning check
for both lab and production routers.

### Layer 4 — Provision tests (lab Pi required)

Tests that run `lankit provision` against the lab Pi and verify the result:

- Pi-hole is reachable at the expected IP
- Ad domain blocked (doubleclick.net → 0.0.0.0)
- DNS resolving (example.com returns a valid answer)
- DNSSEC validating

These use the same DNS smoke tests proposed in ENH-006. Marked `@pytest.mark.provision`.

## Directory structure

```
tests/
├── conftest.py                  # shared fixtures, MockRouterConnection
├── unit/
│   ├── test_config.py           # config loading and validation
│   ├── test_generator.py        # Jinja2 rendering
│   └── test_snapshots.py        # snapshot lifecycle
├── integration/
│   ├── test_apply.py            # apply flow with mock router
│   ├── test_audit.py            # audit parsing with fixture output
│   ├── test_probe.py            # probe with mock router
│   └── test_rollback.py         # rollback flow
├── hardware/
│   ├── test_apply_hw.py         # end-to-end apply on lab router
│   ├── test_failsafe_hw.py      # failsafe timer fires correctly
│   └── test_provision_hw.py     # Ansible + DNS smoke tests
└── fixtures/
    ├── clean/                   # factory-reset router output
    ├── provisioned/             # fully provisioned router output
    └── drifted/                 # router with rogue entries
```

## Running tests

```bash
# offline only (no hardware required)
pytest tests/unit tests/integration

# with lab router
pytest -m hardware

# with lab router + Pi
pytest -m "hardware or provision"

# everything
pytest
```

## Open questions

- Should `MockRouterConnection` be a subclass of `RouterConnection` or implement a
  shared protocol/ABC? A protocol avoids inheritance but requires more structural change.
- Where does fixture capture live — a dedicated `lankit capture-fixtures` command, or a
  one-time manual process documented in this file?
- Should hardware tests run in CI on a self-hosted runner connected to the lab, or
  remain manual-only?
- The older lab MikroTik model is untested — what RouterOS version does it run, and
  does it support all the commands lankit uses?
