# Lab Router Bootstrap Guide (hAP ac)

This is the practical, hardware-level companion to the "Hardware" section of
`docs/test-harness.md`. That doc describes *what* the lab router is for; this
doc is *how* to actually bring one up from a box that used to be something
else (in this case, a retired production hAP ac) into a clean lab router
ready for `lab-network.yml` and fixture capture.

It exists because every quirk below cost real debugging time on first setup.
Treat this as a living doc — add to it the next time something surprising
happens during a reset.

## Factory reset: use the CLI command, not the reset button

Holding the reset button while power-cycling is the commonly cited method,
but it did not produce a clean reset here — the router came back still
accepting the previous (production) admin password. Don't rely on it.

Instead, run the reset from RouterOS itself, while you still have *any*
working SSH/Winbox session (even old production credentials):

```
/system reset-configuration skip-backup=yes
```

Notes:
- **Don't add `no-defaults=yes`.** That produces a fully blank config — no
  bridge, no DHCP server, nothing addressable. `fixtures/clean/` in the test
  harness expects a router with its *default* bridging/DHCP intact and no
  lankit config layered on top, which is what a plain `reset-configuration`
  gives you.
- The command reboots immediately and drops your session with no
  confirmation prompt (RouterOS's usual interactive y/N gate doesn't appear
  when the command runs as a non-interactive SSH exec).
- Everything is wiped: users, passwords, SSH keys, any customized
  default-configuration script. See the next section — this includes things
  you might expect to survive a "reset."
- After reboot, the router comes back as `admin` with a **blank** password,
  which forces RouterOS's interactive first-login flow (set a new password)
  on the next connection. This can't be scripted through a non-interactive
  SSH command — budget for one manual interactive login per reset.

## Quirks encountered, and how we addressed them

### 1. A customized default address does not survive `reset-configuration`

We expected a previously-customized default-configuration (LAN address
`192.168.77.1` instead of the stock `192.168.88.1`, set specifically to
avoid colliding with the production router's identical default address) to
persist across a reset. It didn't — the router came back at the stock
`192.168.88.1`.

**Fix:** after any reset, explicitly re-set the bridge address rather than
assuming a prior customization stuck:

```
/ip address print
/ip address set [find interface=bridge] address=192.168.77.1/24
```

Expect this to drop your current SSH session immediately — you just changed
the address the session itself is using. That's expected, not a hang; don't
reboot the router over it. Reconnect at the new address.

### 2. A stale laptop-side static IP can silently mask real DHCP

After the router's address changed, the controller laptop's wired interface
kept presenting an old `192.168.88.100/24` address with an infinite lease —
left over from an earlier NetworkManager profile edit, not from the router.
NetworkManager will layer a manually-set `ipv4.addresses` entry on top of a
connection whose `ipv4.method` is still `auto`, so `nmcli` can show "auto"
while a stale static address quietly persists and coexists with (or blocks
useful diagnosis of) genuine DHCP leases.

**Fix:** check for and clear any static override before trusting DHCP
behavior:

```
nmcli connection show "Wired connection 1" | grep -E "ipv4.method|ipv4.addresses"
nmcli connection modify "Wired connection 1" ipv4.gateway "" ipv4.addresses ""
nmcli device disconnect enp12s0 && nmcli device connect enp12s0
```

(Substitute your actual connection/interface names.)

### 3. `/ip dhcp-server network` is a dynamic entry — edit the pool instead

Changing the bridge's IP address does not update what the DHCP server hands
out to clients. `/ip dhcp-server network print detail` is generated from
elsewhere and is marked `D` (dynamic); running `set` on it by `find` filter
(e.g. `find address=192.168.88.0/24`) silently does nothing — no error, no
change.

The actual source is the address pool:

```
/ip pool print detail
/ip pool set [find name=default-dhcp] ranges=192.168.77.10-192.168.77.254
```

Even after fixing the pool, the dynamic network entry can still show stale
values. Disabling/enabling the DHCP server did **not** force it to
regenerate in our case. What did work: setting the network entry directly
**by numeric index**, not by `find` filter:

```
/ip dhcp-server network set 0 address=192.168.77.0/24 gateway=192.168.77.1 dns-server=192.168.77.1
```

Verify with `/ip dhcp-server network print detail` before trusting a client
to get a usable lease.

### 4. ed25519 SSH keys are silently unsupported on older RouterOS

This lab router's RouterOS version supports `/user ssh-keys import` for RSA
and DSA only — the flags legend on `/user ssh-keys print detail` literally
reads `Flags: R - RSA, D - DSA`, no ed25519 entry. Importing an ed25519
public key produces **no error and no effect**: the import command exits
clean, but the key never appears in `/user ssh-keys print detail`. This is
easy to mistake for a scp/upload problem when it's actually a silent
algorithm rejection.

**Fix:** generate an RSA key for older lab hardware instead:

```
ssh-keygen -t rsa -b 4096 -f ~/.ssh/id_rsa_lab_router -N "" -C "lankit-lab-router"
```

### 5. RSA key accepted by the router, still rejected by modern OpenSSH clients

Even after a successful RSA import (confirmed present via
`/user ssh-keys print detail`), the SSH client still failed with:

```
debug1: send_pubkey_test: no mutual signature algorithm
```

This RouterOS SSH server only supports the legacy `ssh-rsa` (SHA-1)
signature scheme. OpenSSH 8.8+ disabled `ssh-rsa` client-side by default.
Same root cause affects host-key verification, not just pubkey auth.

**Fix:** explicitly re-enable the legacy algorithm for this host only (don't
do this globally):

```
Host lab-router
    Hostname 192.168.77.1
    User admin
    IdentityFile ~/.ssh/id_rsa_lab_router
    IdentitiesOnly yes
    PubkeyAcceptedAlgorithms +ssh-rsa
    HostKeyAlgorithms +ssh-rsa
```

### 6. known_hosts collisions when an address is reused or a key changes

Because the lab router's address has at various points overlapped with the
production router's default (`192.168.88.1`), or its host key changed
across a reset, standard SSH MITM protection triggers — correctly. Do not
blanket-clear `known_hosts`; scope the fix to the specific IP:

```
ssh-keygen -R 192.168.77.1
```

For the stable day-to-day setup, we added `StrictHostKeyChecking accept-new`
scoped to just the `lab-router` `Host` stanza in `~/.ssh/config` (not
global). This auto-trusts a *new* key without a manual step after each
intentional reset, while still refusing loudly if an *already-trusted* key
suddenly changes — real MITM protection stays intact, only the "expected,
self-inflicted key change from a reset" case is smoothed over.

### 7. Password auth stops working once a key is registered — this is intentional

After importing an SSH key, password login for that same user started
failing even with the correct password. This is not a bug and not a lost
password. `/ip ssh print` shows:

```
always-allow-password-login: no
```

This is RouterOS's default. Once a user has a registered public key, the
SSH server refuses password auth for that user — "password" still appears
as an offered method during protocol negotiation, but the actual credential
check rejects it. Set `/ip ssh set always-allow-password-login=yes` only if
you specifically want password fallback to coexist with key auth; otherwise
leave it — key-only is the more secure default and is what this harness
uses.

## Condensed bootstrap sequence

Putting the above together, end to end, for standing up a hAP ac (or
similar older-RouterOS device) as a lab router from scratch:

1. Wire the router's LAN-side port into the controller machine. Use a
   cellular hotspot (not the production Wi-Fi) for the controller's internet
   access during setup, to avoid any address collision with production.
2. Connect at whatever address/credentials currently work (factory default
   is `192.168.88.1`, admin, blank password) and run:
   ```
   /system reset-configuration skip-backup=yes
   ```
3. Reconnect interactively at `192.168.88.1`, complete the forced first-login
   password-set flow.
4. Move the router off the production-colliding default address:
   ```
   /ip address set [find interface=bridge] address=192.168.77.1/24
   ```
   (session will drop — reconnect at the new address)
5. Fix the DHCP pool and network entry to match (see quirk 3 above):
   ```
   /ip pool set [find name=default-dhcp] ranges=192.168.77.10-192.168.77.254
   /ip dhcp-server network set 0 address=192.168.77.0/24 gateway=192.168.77.1 dns-server=192.168.77.1
   ```
6. Generate and import a dedicated RSA key (see quirks 4-5):
   ```
   ssh-keygen -t rsa -b 4096 -f ~/.ssh/id_rsa_lab_router -N "" -C "lankit-lab-router"
   scp ~/.ssh/id_rsa_lab_router.pub admin@192.168.77.1:lab_router_rsa.pub
   ssh admin@192.168.77.1 "/user ssh-keys import public-key-file=lab_router_rsa.pub user=admin"
   ```
7. Add the `lab-router` stanza to `~/.ssh/config` (see quirk 5-6) and confirm
   key-based login works with no password prompt.
8. Point `lab-network.yml`'s `router.ip` and `router.ssh_key` at the new
   address and key, and capture the `fixtures/clean/` baseline before
   running any `lankit apply` against it.

## Related

- `docs/test-harness.md` — what the lab router is for and how it fits the
  automated test layers
- `TESTING.md` — the manual commissioning runbook this guide is *not* a
  substitute for; that one governs first-time setup on a **production**
  router
