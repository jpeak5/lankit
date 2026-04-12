#!/usr/bin/env python3
"""
Configure pihole.toml with lankit-managed settings.
Run by Ansible as a script module.
Exits 0 always; prints JSON for Ansible changed detection.

Arguments (passed via environment variables set by Ansible):
  LANKIT_DNS_SERVER_IP    — IP of this Pi-hole host
  LANKIT_INTERNAL_DOMAIN  — e.g. "internal"
  LANKIT_PRIVACY_LEVEL    — 0 (full) | 1 (anonymous) | 3 (none)
  LANKIT_QUERY_RETENTION  — e.g. "7d"
  LANKIT_BLOCK_APPLE_RELAY — "true" | "false"
"""

import json
import os
import sys

try:
    import toml
except ImportError:
    sys.exit("toml module not available — run: pip3 install toml")

PIHOLE_TOML = "/etc/pihole/pihole.toml"
CHANGED = False


def set_nested(config, path, value):
    global CHANGED
    keys = path.split(".")
    d = config
    for k in keys[:-1]:
        d = d.setdefault(k, {})
    if d.get(keys[-1]) != value:
        d[keys[-1]] = value
        CHANGED = True


def get_env(key, default=None):
    val = os.environ.get(key, default)
    if val is None:
        sys.exit(f"Required environment variable not set: {key}")
    return val


with open(PIHOLE_TOML, "r") as f:
    config = toml.loads(f.read())

dns_server_ip   = get_env("LANKIT_DNS_SERVER_IP")
internal_domain = get_env("LANKIT_INTERNAL_DOMAIN", "internal")
privacy_level   = int(get_env("LANKIT_PRIVACY_LEVEL", "0"))
query_retention = get_env("LANKIT_QUERY_RETENTION", "7d")
block_relay     = get_env("LANKIT_BLOCK_APPLE_RELAY", "false").lower() == "true"

# DNS upstream: Unbound on localhost
set_nested(config, "dns.upstreams", ["127.0.0.1#5335"])
set_nested(config, "dns.domainNeeded", True)
set_nested(config, "dns.expandHosts", True)
set_nested(config, "dns.interface", "eth0")

# Privacy level:
#   0 = full logging (show everything)
#   1 = hide domains (anonymous)
#   3 = no logging
set_nested(config, "misc.privacylevel", privacy_level)

# Query retention (converted from "7d" / "24h" to seconds)
def parse_retention(s):
    s = s.strip()
    if s.endswith("d"):
        return int(s[:-1]) * 86400
    if s.endswith("h"):
        return int(s[:-1]) * 3600
    return int(s)

set_nested(config, "database.maxlogage", parse_retention(query_retention))

# Web server port — run behind Caddy
set_nested(config, "webserver.port", "8081o,[::]:8081o")

# Apple Private Relay blocking
# Pi-hole blocks by adding these domains to the blocklist group
APPLE_RELAY_DOMAINS = [
    "mask.icloud.com",
    "mask-h2.icloud.com",
    "mask-canary.icloud.com",
]
# Note: actual blocklist management is done via the Pi-hole API/DB,
# not pihole.toml. This flag is read by the Ansible task that follows.
# We write it here so it's recorded in the config for reference.
set_nested(config, "lankit.block_apple_relay", block_relay)

if CHANGED:
    with open(PIHOLE_TOML, "w") as f:
        toml.dump(config, f)

print(json.dumps({"changed": CHANGED}))
