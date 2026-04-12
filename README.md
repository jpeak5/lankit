# lankit

lankit is a configuration toolkit for the MikroTik hAP ax³ that generates RouterOS scripts and provisions Pi-hole + Unbound from a single YAML file. It's for people who write code or run home labs, understand what VLANs are supposed to do, and have found raw RouterOS too arcane to configure reliably. Declare your segments and permissions in `network.yml`; lankit handles the translation into router commands, DNS configuration, and firewall rules — with rollback before every change. It is not a GUI, it does not support other router brands, and it does not manage your network for you.

## What it does

- **Segments** — VLAN-isolated networks (trusted, IoT, guest, servers, etc.)
  with per-segment DNS filtering, internet access rules, client isolation,
  and bandwidth limits
- **DNS** — Pi-hole (ad blocking, query logging) backed by Unbound (full
  recursive resolution, DNSSEC) in a single-interface design that requires
  no changes when VLANs are added
- **Safety** — every `lankit apply` snapshots the router config first;
  `lankit rollback` restores it. A printable rollback card covers the
  no-laptop scenario.
- **CLI** — `lankit discover`, `lankit extend`, `lankit rules`, `lankit diagram`,
  and more

## Requirements

- Python 3.11+
- MikroTik router (tested on hAP ax³) with SSH access
- Raspberry Pi running Raspberry Pi OS (for Pi-hole + Unbound)
- `pip install -e .` (see below)
- Ansible (for `lankit provision`)
- graphviz system package (optional, for `lankit diagram`)

## Install

```bash
git clone <this repo>
cd kit
pip install -e .
lankit --help      # verify install
```

## Quick start

```bash
cp network.yml my-network.yml      # or: lankit discover --new
# edit my-network.yml — fill in all CHOOSE fields

lankit overview                    # verify config parsed correctly
lankit generate                    # render RouterOS scripts
lankit diagram --view              # visual sanity check
lankit rollback-card               # print and keep near router
lankit apply --dry-run             # confirm SSH works
lankit apply                       # push to router
lankit provision                   # set up Pi-hole + Unbound
```

See `TESTING.md` for the full phased testing progression.

## Commands

| Command | What it does |
|---|---|
| `lankit discover [--new]` | Scan for connected devices; `--new` runs the setup wizard |
| `lankit overview` | Show segments, hosts, permissions, and privacy settings |
| `lankit generate` | Render RouterOS `.rsc` scripts from `network.yml` |
| `lankit apply` | Push scripts to router (snapshots first, prompts to keep/revert) |
| `lankit commit` | Save current router config as a named snapshot |
| `lankit rollback` | Restore the pre-apply snapshot |
| `lankit restore` | Restore any snapshot interactively |
| `lankit provision` | Run Ansible to set up Pi-hole + Unbound |
| `lankit rules` | Show generated firewall rules (filterable by segment/unit) |
| `lankit diagram` | Generate a network topology diagram |
| `lankit rollback-card` | Generate a printable emergency recovery card |
| `lankit test-failsafe` | Verify the scheduler-based failsafe auto-revert works |
| `lankit extend` | Interactive wizard to add a new segment |

## Design

See `DESIGN.md` for the full architecture, philosophy, and design decisions.

## File structure

```
kit/
├── network.yml              # your network declaration (edit this)
├── network.schema.json      # JSON Schema for validation
├── lankit/
│   ├── core/
│   │   ├── config.py        # load + validate network.yml
│   │   ├── generator.py     # render Jinja2 → .rsc scripts
│   │   ├── router.py        # RouterOS SSH connection
│   │   └── snapshots.py     # local config snapshot management
│   └── cli/
│       └── commands/        # one file per lankit subcommand
├── ansible/
│   ├── site.yml             # main playbook (run by lankit provision)
│   └── roles/
│       └── dns-server/      # Pi-hole + Unbound role
└── ansible/generated/       # rendered .rsc scripts (git-ignored)
```

## License

MIT — see [LICENSE](LICENSE).
