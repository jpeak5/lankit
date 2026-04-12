# ENH-012: Router SSH connection failure prints generic error with no diagnosis steps

**Persona(s):** Alex, Morgan, Riley
**Surface:** CLI — `lankit apply`, `lankit audit`, `lankit probe`, any command that connects to the router
**Priority:** High

## Problem

When any command fails to SSH into the router, it prints:

```
Router error: <raw paramiko/SSH exception message>
```

The raw exception message is typically one of:
- `[Errno 111] Connection refused`
- `Authentication failed`
- `No route to host`
- `timed out`

None of these tell the user what to check. Alex has never configured SSH keys. Morgan doesn't know what "authentication failed" means in context. Riley remembers that SSH "used to work" but doesn't know why it stopped after the firmware update.

The `RouterError` wrapper in `apply.py` adds one extra line: "If the failsafe scheduler is still active it will revert in ≤120s." — but only for `apply`. Other commands just print the error and exit.

## Proposed fix

Classify the SSH error type and print a specific diagnosis block:

**Connection refused / timed out:**
```
Router error: Could not connect to 192.168.88.1 (Connection refused)

  Is the router powered on and reachable?
  • Try pinging: ping 192.168.88.1
  • Are you on the correct network? (not VPN, not remote)
  • Is SSH enabled? RouterOS: /ip service enable ssh
```

**Authentication failed:**
```
Router error: Authentication failed for admin@192.168.88.1

  lankit uses key-based auth. Check:
  • SSH key path in network.yml: router.ssh_key = ~/.ssh/lankit
  • Key permissions: chmod 600 ~/.ssh/lankit
  • Key is accepted by the router: ssh-copy-id -i ~/.ssh/lankit admin@192.168.88.1
  • Or verify: ssh -i ~/.ssh/lankit admin@192.168.88.1
```

**No route to host:**
```
Router error: No route to 192.168.88.1

  The router IP in network.yml (192.168.88.1) is unreachable from here.
  • Is your laptop connected to the router's LAN (not guest WiFi)?
  • Has the router IP changed? Check: lankit discover --router <new-ip>
```

## Acceptance criteria

- [ ] `RouterError` is caught and classified into at least three categories: refused/timeout, auth failure, no route
- [ ] Each category prints a numbered checklist of things to verify
- [ ] The SSH key path from `network.yml` is included in the auth failure message
- [ ] All commands that catch `RouterError` use the same classification logic (shared helper)
