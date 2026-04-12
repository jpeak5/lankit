# lankit — User Personas

Five representative users whose needs and friction points guide lankit's UX decisions.

---

## Alex — The Curious Homeowner

**Background:** Software developer by trade. Comfortable in the terminal, reads README carefully, googles errors. Motivated by IoT isolation.

**Goal:** Get `lankit apply` working in an evening.

**Comfort level:** High on tooling; moderate on networking concepts.

**Friction:**
- Jargon without definitions (VLAN, DHCP, egress, kit: tags)
- Steps that assume prior networking knowledge
- Errors that don't say what to do next
- No clear "did it work?" signal after a command succeeds

---

## Morgan — The Privacy-Focused Professional

**Background:** Non-technical job, tech-curious. Motivated by ad blocking and ISP privacy. Has a Raspberry Pi, wants Pi-hole.

**Goal:** Block ads, keep DNS off ISP servers, ideally from a single config file.

**Comfort level:** Can follow step-by-step instructions; uncomfortable when things go off-script.

**Friction:**
- Any error that requires interpreting raw output
- Config files with no explanation of what a setting does
- No feedback on whether it worked (Pi-hole actually running? DNS actually filtering?)
- Privacy settings labeled with jargon she can't map to real-world meaning

---

## Sam — The Home Lab Builder

**Background:** IT professional or CS student. pfSense experience. Reads DESIGN.md before README. Will file bugs.

**Goal:** Full VLAN segmentation, WireGuard, custom firewall rules.

**Comfort level:** High. Reads source code when curious.

**Friction:**
- Abstractions that hide what's happening on the router
- No escape hatch to raw RouterOS (can't see the generated scripts easily)
- Opinionated defaults with no override mechanism
- Audit/probe output that uses lankit tags instead of RouterOS identifiers
- Missing change preview before `lankit apply` runs

---

## Jordan — The Small Office User

**Background:** Runs a small company from home. Needs client WiFi isolation for compliance. Has an IT contact but wants to self-serve day-to-day.

**Goal:** Guest/client WiFi that's provably isolated from the company LAN. Minimal ongoing maintenance.

**Comfort level:** Intermediate. Comfortable with web UIs; less comfortable with CLI.

**Friction:**
- Undocumented failure modes (what breaks if the Pi goes down?)
- No rollback story that non-technical staff can execute
- Anything that requires starting from scratch after a firmware update
- No way to confirm isolation is actually working without network expertise

---

## Riley — The Returning User

**Background:** Set up lankit 6 months ago, mostly forgot how it works. Needs to add a new device or debug something after a firmware update.

**Goal:** Make a targeted change without breaking anything. Understand what's different since the last run.

**Comfort level:** Was comfortable at setup; now uncertain. Doesn't want to re-read all docs.

**Friction:**
- No way to see current router state vs. what network.yml says (drift)
- Commands that do too much at once with no dry-run
- Unclear errors after months of no changes (e.g. SSH key permissions changed)
- Can't remember which command is safe to re-run vs. which is destructive
- No changelog or "what changed since last apply" output
