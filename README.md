# lankit

lankit is a configuration toolkit for the MikroTik hAP ax³ that generates RouterOS scripts and provisions Pi-hole + Unbound from a single YAML file. It's for people who write code or run home labs, understand what VLANs are supposed to do, and have found raw RouterOS too arcane to configure reliably. Declare your segments and permissions in `network.yml`; lankit handles the translation into router commands, DNS configuration, and firewall rules — with rollback before every change. It is not a GUI, it does not support other router brands, and it requires you to make decisions: lankit executes them, it does not make them for you.

## What it does

- **Segments** — VLAN-isolated networks (trusted, IoT, guest, servers, etc.)
  with per-segment DNS filtering, internet access rules, client isolation,
  and bandwidth limits
- **DNS** — Pi-hole (ad blocking, query logging) backed by Unbound (full
  recursive resolution, DNSSEC). Adding a new VLAN requires no DNS
  reconfiguration.
- **Portals** — optional web interfaces served by Caddy on a second Pi
  (`app_server`): `me.internal` (device self-service), `apps.internal`
  (landing page), `register.internal` (MAC registration). Currently
  placeholder pages; dynamic features tracked in the issue queue.
- **Safety** — every `lankit apply` snapshots the router config first;
  `lankit rollback` restores it. If you push a bad config and lose WiFi,
  you can't reach the router from your laptop — the printable rollback card
  covers that scenario with step-by-step recovery instructions.

## Requirements

- Python 3.11+
- MikroTik router (tested on hAP ax³) with SSH access enabled
- Raspberry Pi running Raspberry Pi OS, connected to the router via ethernet
  (hosts Pi-hole + Unbound; required for `lankit provision`)
- Second Raspberry Pi (optional) for the app server — portals, file shares.
  Set `hosts.app_server.enabled: true` in `network.yml` when you have it.
- graphviz system package (optional, for `lankit diagram`)

Ansible is installed automatically as part of `pip install -e .`.

## Install

```bash
git clone https://github.com/jpeak5/lankit.git
cd lankit
pip install -e .

# Verify:
lankit --version
```

If `lankit` is not found after install, your `~/.local/bin` may not be in PATH:

```bash
export PATH="$HOME/.local/bin:$PATH"   # add to ~/.bashrc or ~/.zshrc to persist
```

## Quick start

### Option A — Guided setup (recommended for first-time users)

```bash
lankit discover --new    # walks you through creating network.yml (~10 minutes)
```

### Option B — Edit the template directly

```bash
cp network.yml.dist network.yml
# open network.yml and fill in all CHOOSE fields
```

Then:

```bash
lankit overview                    # verify config parsed correctly
lankit generate                    # render RouterOS scripts
lankit diagram --view              # visual sanity check
lankit rollback-card               # print and keep near router
lankit apply --dry-run             # confirm SSH works
lankit apply                       # push to router
lankit provision                   # set up Pi-hole + Unbound
```

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
| `lankit provision` | Run Ansible to set up Pi-hole + Unbound (and app server if enabled) |
| `lankit rules` | Show generated firewall rules (filterable by segment/unit) |
| `lankit diagram` | Generate a network topology diagram |
| `lankit rollback-card` | Generate a printable emergency recovery card |
| `lankit test-failsafe` | Verify the scheduler-based failsafe auto-revert works |
| `lankit extend` | Interactive wizard to add a new segment |

## Design

See `DESIGN.md` for the full architecture, philosophy, and design decisions.

## File structure

```
lankit/
├── network.yml.dist         # template — copy to network.yml and fill in CHOOSE fields
├── network.yml              # your private config (gitignored)
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
│       ├── dns-server/      # Pi-hole + Unbound role
│       ├── caddy/           # Caddy web server (app_server host)
│       └── portal/          # portal placeholder pages
└── ansible/generated/       # rendered .rsc scripts (git-ignored)
```

## License

MIT — see [LICENSE](LICENSE).
