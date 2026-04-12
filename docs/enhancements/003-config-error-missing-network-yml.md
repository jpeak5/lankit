# ENH-003: ConfigError for missing network.yml doesn't tell user how to create one

**Persona(s):** Alex, Morgan
**Surface:** CLI — all commands, `lankit/core/config.py`
**Priority:** High

## Problem

When `network.yml` doesn't exist in the current directory and `LANKIT_CONFIG` is not set, every lankit command fails with:

```
Config error: network.yml not found.
Run lankit from your lankit directory, or set LANKIT_CONFIG.
```

The message assumes the user knows what "your lankit directory" means and that `network.yml` is something they need to create. A first-time user (Alex just cloned the repo; Morgan following a blog post) has no idea:

- That they need to copy or create `network.yml` before any command works
- That the repo ships a template they can start from
- That `lankit discover --new` exists as a guided wizard alternative

The error also surfaces for returning users (Riley) who have accidentally changed directories.

## Proposed fix

Expand the error message to be actionable:

```
Config error: network.yml not found in the current directory.

To get started:
  cp network.yml my-network.yml     # start from the example template
  lankit discover --new             # or run the setup wizard

Then run lankit from the directory containing network.yml,
or set LANKIT_CONFIG=/path/to/your-network.yml.
```

The `cp` example should only appear if a `network.yml` exists in the repo root (i.e., the user is likely in a subdirectory).

## Acceptance criteria

- [ ] Error message names the two ways to create a config (cp template, discover --new)
- [ ] Error message mentions `LANKIT_CONFIG` as an alternative to changing directories
- [ ] Message is actionable without googling or reading the README
