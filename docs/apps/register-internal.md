# register.internal — Device Registration Portal

## Purpose

Lets household members name their devices — particularly important for devices using MAC
address randomization, which would otherwise appear as a new unknown device on every
reconnection. In v2, serves as the portal a quarantined device is directed to when it
first connects.

## Discovery

**v1:** Any device that knows to visit `register.internal` can register. No automatic
redirection.

**v2:** Quarantined devices must be able to discover this portal without prior knowledge.
The mechanism is a captive-portal DNS redirect: all DNS queries from the quarantine segment
resolve to app_server. The browser's captive portal detection (present on iOS, Android,
Windows, macOS) will open register.internal automatically on connection. This requires a
specific dnsmasq rule on dns_server, deployed by Ansible, that is active only for the
quarantine segment's IP range.

This is the critical path for v2. Do not ship quarantine enforcement without it.

## Routes

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Registration page |
| `POST` | `/register` | Submit name; returns updated page or validation error |
| `GET` | `/status` | HTML fragment: registration state for this MAC (HTMX target) |

## Page: GET /

Shows: current IP, device identifier (MAC), current name if any.

**Pre-submission:**
- "What should we call your device?"
- Name input (alphanumeric + hyphens, 1–30 chars)
- Submit: "Register this device"

**Post-submission (v1 — immediate):**
- "Done. This device is now known as `<name>`."
- Link to me.internal

**Post-submission (v2 — pending approval):**
- "Got it. Your device will be connected once it's approved."
- HTMX polls `/status` every 15 seconds. When status changes to `approved`, page reloads.
- After 10 minutes of polling with no approval: show "Still waiting? Contact [household_name]
  network admin." Polling stops.

## v1 scope

1. Device visits register.internal from any segment
2. Sees its MAC, current IP, and current name (if any)
3. Enters a name
4. Name is applied immediately: DNS record added to Pi-hole, DHCP lease comment updated
5. Confirmation shown

v1 auto-approves on submission. No approval queue.

## v2 scope (quarantine active)

1. Quarantined device is redirected to register.internal by captive portal DNS
2. User names the device and optionally indicates intended use (personal / work / IoT)
3. Submission creates a `registrations` row with `status='pending'`
4. Admin reviews and approves (via `lankit register approve <mac>` CLI or dashboard)
5. On approval: DHCP static reservation created on router, device moved to appropriate segment
6. Page detects approval via HTMX polling and shows confirmation

## MAC retrieval

MAC address is looked up from the requesting client IP via `GET /api/network/devices` on
the Pi-hole API. See `architecture.md` for the lookup pattern. The result is cached; cache
is invalidated on registration or rename.

The MAC shown to the user is the network-specific MAC. On devices using per-network MAC
randomization (Apple, Android), this MAC is stable for this network but differs on other
networks. The UI label is "Device identifier" — not "MAC address."

## Idempotence

- If the requesting MAC already has an approved registration with the same name, return
  the confirmation view without inserting.
- If already pending: return the pending view, do not insert a second row.
- The DHCP static reservation on the router uses the check-then-add pattern:

```
:if ([/ip dhcp-server lease find mac-address="<mac>"] = "") do={
    /ip dhcp-server lease add mac-address="<mac>" address=<ip> server=dhcp1 comment=<name>
} else={
    /ip dhcp-server lease set [find mac-address="<mac>"] comment=<name>
}
```

## Copy tone

Non-technical. Do not use: "MAC address," "VLAN," "segment," "DHCP," "quarantine."
Use: "device identifier," "recognized," "connected," "your network."
