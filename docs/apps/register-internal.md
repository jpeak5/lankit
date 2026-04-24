# register.internal — Device Registration Portal

## Purpose

Lets household members claim and name their devices — particularly important
for Apple devices using MAC randomization, which would otherwise appear as a
new unknown device on every connection.

This is the portal a device is directed to when it lands in quarantine.
It is also useful from any segment: name your device before network admin
has to figure out what "iPhone (2)" is.

## No prior art

`register.internal` is new. There is no equivalent in
`~/Documents/code/network/portal/`. The rename functionality in `me.internal`
handles the name→DNS mapping, but registration is a different flow:

- **Rename** (me.internal): you already have full access, you want to name yourself
- **Register** (register.internal): you may be quarantined or new; you want to
  identify yourself and request placement on the network

## v1 scope (pre-VLAN enforcement)

With all eight VLANs defined but VLAN-based quarantine not fully enforced for
all device types, v1 keeps it simple:

1. Device visits `register.internal` from any segment
2. Sees its MAC address, current IP, and current name (if any)
3. Enters a friendly name
4. Name is pinned: DHCP lease comment updated on router, DNS record added to Pi-hole
5. Confirmation shown

This is identical to the rename flow in `me.internal`, surfaced as a standalone
page with cleaner copy aimed at non-technical users ("What should we call your device?").

## v2 scope (post-VLAN enforcement, quarantine active)

When a device in quarantine visits `register.internal`:

1. Shows: "Your device isn't recognized yet. Give it a name to request access."
2. User enters name + optionally selects intended use (personal device / work / IoT)
3. Submission creates a pending registration record
4. Admin reviews in Pi-hole or via `lankit` CLI
5. On approval: DHCP static reservation created, device moved to appropriate segment
6. Device notified (page auto-polls; shows "You're all set" when approved)

v2 requires a persistent approval queue (SQLite table) and a `lankit register approve`
CLI command or dashboard integration.

## Routes

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Registration page |
| `POST` | `/register` | Submit name; returns success or validation error |
| `GET` | `/status` | JSON: registration state for this device (pending/approved/none) |

## Page: GET /

**Pre-approval view:**
- "What should we call your device?"
- Shows current MAC, IP, current name (if any)
- Name input (same validation as rename: alphanumeric + hyphens, 1-30 chars)
- Submit button: "Register this device"

**Post-submission view (v1):**
- "Done. Your device is now known as `<name>`."
- Link to me.internal

**Post-submission view (v2, pending approval):**
- "Got it. Once your device is approved, you'll be connected automatically."
- Auto-polls `/status`; reloads when state changes to `approved`

## Data model

```sql
-- v1: just the rename log (shared with me.internal)

-- v2 addition:
CREATE TABLE registrations (
    id INTEGER PRIMARY KEY,
    mac TEXT NOT NULL,
    ip TEXT NOT NULL,
    requested_name TEXT NOT NULL,
    requested_segment TEXT,          -- optional: user's stated intent
    status TEXT NOT NULL DEFAULT 'pending',  -- pending | approved | rejected
    submitted_at TEXT NOT NULL,
    reviewed_at TEXT,
    reviewed_by TEXT                 -- "admin" or future: specific user
);
```

## Copy tone

Non-technical. Avoid "MAC address" in primary text (show it but label it
"Device identifier"). Avoid "VLAN", "segment", "DHCP". Use: "recognized",
"connected", "your device."

## Open questions

- Should register.internal be reachable from quarantine? Post-VLAN, quarantine
  has `internet: none` — the firewall would need a specific rule permitting
  quarantine → app_server:80. Add as a post-VLAN firewall rule.
- Should approved registrations auto-approve on name submission (v1 mode) or
  require explicit admin action? v1: auto-approve. v2: explicit.
