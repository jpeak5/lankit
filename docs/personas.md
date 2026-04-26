# lankit — User Personas

Two groups of users shape lankit's design.

**Installers** set up and maintain the network. Their friction points drive the CLI and
configuration UX. **Household members** live on the network day-to-day without managing it.
They are the primary audience for the portals — and the population most harmed when something
goes wrong. Their natural usage patterns are the closest thing to a live test suite: the
failure scenarios they generate, and whether they can recover without admin intervention,
are the real measure of whether the portals work.

---

## Installers

---

## Priya — The Infrastructure-Curious Homeowner

**Background:** University sysadmin by day. Comfortable in the terminal and familiar with Linux
networking. Motivated by keeping work VMs and personal lab experiments on separate segments
from the rest of the household.

**Goal:** Get `lankit apply` working in an evening; understand what it actually did.

**Comfort level:** High on tooling; moderate on MikroTik-specific concepts.

**Friction:**
- Jargon without definitions (VLAN, DHCP, egress, kit: tags)
- Steps that assume MikroTik prior experience
- Errors that don't say what to do next
- No clear "did it work?" signal after a command succeeds

---

## Dale — The Privacy-Aware Parent

**Background:** High school librarian. Co-parents two kids; the household has two WiFi-heavy
teenagers and a shared NAS neither adult wants the kids touching. Tech-curious but not
comfortable at the terminal. Wants ad blocking and per-device controls. Found Pi-hole via
a blog post.

**Goal:** Block ads, keep kids' devices off the adult network, get DNS off ISP servers.

**Comfort level:** Can follow step-by-step instructions; stops cold when anything goes off-script.

**Friction:**
- Any error that requires interpreting raw output
- Config files with no explanation of what a setting does
- No feedback on whether it worked (Pi-hole actually filtering? DNS actually changed?)
- Privacy settings labeled with jargon that can't be mapped to real-world consequences

---

## Tariq — The Security-Minded Tinkerer

**Background:** Penetration tester at a mid-size consultancy. OPNsense background. Reads
DESIGN.md before README. Will file bugs and may open PRs.

**Goal:** Full VLAN segmentation, WireGuard, auditable firewall rules.

**Comfort level:** High. Reads generated RouterOS scripts to verify correctness.

**Friction:**
- Abstractions that hide what's actually pushed to the router
- No escape hatch to view generated RouterOS scripts before apply
- Opinionated defaults with no override mechanism
- Probe output that uses lankit tags instead of RouterOS identifiers
- Missing change preview before `lankit apply` runs

---

## Renata — The Home Business Owner

**Background:** Runs a tutoring business from home. Students bring their own devices; she
needs them isolated from her personal network for liability reasons. Has an IT-savvy contact
but wants to self-serve day-to-day.

**Goal:** Student WiFi that's provably isolated from the home network. Minimal ongoing maintenance.

**Comfort level:** Intermediate. Comfortable with web UIs; avoids the CLI when possible.

**Friction:**
- Undocumented failure modes (what breaks if the Pi goes down?)
- No rollback path she can execute herself
- Anything that requires starting from scratch after a router firmware update
- No way to confirm isolation is actually working without networking expertise

---

## Yemi — The Returning Installer

**Background:** Product manager at a software company. Set up lankit about a year ago, then
did a home remodel. Added smart home devices during the remodel and now wants to put them
on their own segment. Mostly remembers how lankit works but has forgotten the specifics.

**Goal:** Make a targeted change without breaking anything. Understand what's different since
the last run.

**Comfort level:** Was comfortable at setup; now uncertain. Doesn't want to re-read all docs.

**Friction:**
- No way to see current router state vs. what network.yml says (drift)
- Commands that do too much at once with no dry-run
- Unclear errors after months of no changes (e.g., SSH key permissions changed)
- Can't remember which command is safe to re-run vs. which is destructive
- No changelog or "what changed since last apply" output

---

## Household Members

These personas do not configure anything. They connect devices, visit portals when something
seems wrong, and encounter the network's failure modes before the installer does. Each one
naturally exercises a distinct set of resilience scenarios.

---

## Kwame — The Work-From-Home Partner

**Household:** Priya's partner. Not technical.

**Background:** Freelance illustrator. Spends most of the day on video calls, large file
transfers, and cloud storage syncs. Doesn't think about the network until it stops working,
at which point it is an emergency.

**Network encounter:** Heavy passive user. Joins meetings, uploads large files, streams
reference material. Occasionally visits me.internal when something loads slowly or ads
reappear, but doesn't always know what he's looking at.

**Failure scenarios generated:**
- Pi-hole restarts mid-call; DNS stops resolving; Kwame sees "no internet" with no context
- Bypass expires 10 minutes into a two-hour client presentation
- Work laptop gets placed on the wrong segment; cloud sync stops working silently
- me.internal shows "98% filtered" on a day when he's convinced something is broken

**Resilience question:** Can Kwame determine on his own whether the network is the problem,
and take one corrective action, without calling Priya?

---

## Seren — The Limit-Tester

**Household:** Dale's 14-year-old. Splits time between both co-parents' homes.

**Background:** Old enough to be curious about why certain sites are blocked, motivated to
find out. Has a school-issued Chromebook and a personal phone; both connect to the home
network. Doesn't think of this as adversarial — just curious and persistent.

**Network encounter:** Active and probing. Notices when something is blocked, tries different
approaches, compares behavior on the Chromebook versus the phone.

**Failure scenarios generated:**
- Visits me.internal, discovers the bypass flow, applies it before Dale intends
- School Chromebook uses a randomized MAC; appears as a new unknown device on every reconnect
- Registers the phone under a benign name, then renames it; tests whether changes propagate
- Finds that a blocked domain is reachable via IP address, bypassing DNS filtering entirely

**Resilience question:** Do the controls hold under casual circumvention attempts? Does each
failure mode surface to Dale rather than going unnoticed?

---

## Aiko — The Overnight Guest

**Household:** Tariq's housemate's visiting friend. Entirely new to the network.

**Background:** Graduate student, visiting for a long weekend. Arrives in the evening, pulls
out a phone, and expects WiFi to work the same way it does everywhere else. Has never heard
of Pi-hole or VLANs. Not adversarial — just unfamiliar.

**Network encounter:** Connects to the guest SSID, potentially gets a captive portal redirect,
visits register.internal if directed there. Leaves in two days.

**Failure scenarios generated:**
- Phone uses a randomized MAC; connects fine the first night, appears as an unknown device again the next morning
- A streaming app fails because its ad SDK domain is blocked by Pi-hole
- Guest network blocks something she needs; no self-service fix is available to her
- register.internal is the first page she sees; she has no context for what it's asking

**Resilience question:** Can a first-time visitor get onto the network and stay on it for a
weekend visit without requiring Tariq or his housemate to intervene?

---

## Clem — The Household Manager

**Household:** Yemi's partner.

**Background:** Nurse. Manages household logistics, is capable and organized under pressure.
Became the de facto first responder for network problems during Yemi's work travel. Has the
rollback card on the fridge. Does not have SSH access and does not want it.

**Network encounter:** Uses the network normally, but is occasionally left alone with a problem.
The smart home devices Yemi added during the remodel have a habit of getting quarantined after
firmware updates change their behavior.

**Failure scenarios generated:**
- Smart thermostat goes offline after a firmware update; heating stops responding; Yemi is
  traveling and unreachable for several hours
- A family member's new phone gets quarantined; Clem needs to register it herself
- The whole network goes down; Clem has the rollback card but has never used it
- me.internal shows an unexpected device on the network; Clem wants to know if it's a problem

**Resilience question:** Can Clem restore a broken device to the network, and determine whether
an unfamiliar device is a threat, without technical assistance?
