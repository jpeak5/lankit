# ENH-016: Rollback card does not include WiFi passwords or reconnect steps

**Persona(s):** Jordan, Morgan, Riley
**Surface:** CLI — `lankit rollback-card`
**Priority:** Medium

## Problem

The emergency rollback card covers router recovery steps, credentials, segment gateways, and DNS server info. However, it does not include:

1. The WiFi SSIDs for each segment
2. Instructions for how to reconnect a laptop to the management network after a factory reset
3. The Pi-hole admin URL and how to verify it's running

For Jordan's small office scenario: after a factory reset and re-apply, someone needs to reconnect all client devices. The rollback card is the "keep near the router" artifact, but it doesn't contain the WiFi names — that's on the password card. Two printed cards are required, and the rollback card doesn't reference the password card.

For Morgan: after a recovery, she needs to know her Pi-hole is at `http://10.40.0.2/admin` — this isn't on the card.

## Proposed fix

Add two sections to the rollback card:

**WiFi Networks section** (using SSIDs from network.yml — passwords omitted for security, reference password card):
```
WIFI NETWORKS
──────────────────────────────────────────────────────────────────────────
  trusted        SSID: MyHome          (see password card for key)
  iot            SSID: MyHome-IoT      (see password card for key)
  guest          SSID: MyHome-visitors (see password card for key)
  ...

  Full passwords: see the WiFi password card (lankit password-card)
```

**Services section:**
```
SERVICES (after recovery)
──────────────────────────────────────────────────────────────────────────
  Pi-hole dashboard:  http://10.40.0.2/admin
  Verify DNS:         nslookup google.com 10.40.0.2
```

Also add a line at the bottom: "Print companion: lankit password-card (WiFi credentials)."

## Acceptance criteria

- [ ] Rollback card includes a WIFI NETWORKS section with SSID names (no passwords)
- [ ] WIFI NETWORKS section references the password card for credentials
- [ ] Rollback card includes a SERVICES section with Pi-hole URL
- [ ] Card includes a reminder to regenerate both cards after provisioning
