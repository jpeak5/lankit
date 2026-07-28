# lankit — v1.0 Readiness Review

*Written 2026-07-06. A review of the scaffolding (mission/personas/design/agents/enhancement queue), a safety audit against the bar "it should be as close to impossible as a home-router tool can get for a working network to actually break," a scope cut, and a sequenced plan to v1.0. Every claim cites the file it came from.*

---

## TL;DR

**The code is in better shape than the paperwork says; the paperwork is in worse shape than it looks; and the single biggest v1.0 blocker is not in any queue.**

1. **The headline safety gap:** every automatic recovery path — the failsafe scheduler, `lankit rollback`, `lankit restore` — "restores" by running `/import` of a raw `/export verbose` snapshot over the live config. RouterOS import is additive and halts on the first error (typically a duplicate `add`). It cannot remove rules a bad apply added, which is precisely what recovery from a bad apply requires. `lankit test-failsafe` doesn't catch this because its revert command is a targeted rule-remove, not a snapshot import — the drill certifies the timer, not the revert. This gap is known (the "full reset rollback" decision was deferred) but appears nowhere in `docs/enhancements/` or the GitHub queue, and it sits directly under the rollback-first non-negotiable in `docs/mission.md:66`. It is the v1.0 gate.

2. **The scaffolding drifted, in a diagnosable way.** The persona cast was renamed (Alex/Morgan/Sam/Jordan/Riley → Priya/Dale/Tariq/Renata/Yemi) without back-updating the 25 enhancement docs or the `lankit-ux` agent that seeds the old cast. DESIGN.md documents a CLI, a comment convention (`kit:` vs the actual `lankit:`), a core-library layout, and a FastAPI portal architecture that no longer exist. The enhancement queue overstates current gaps because fixes landed without closing the paperwork (ENH-004 and ENH-019 are fixed in code; their issues are open). The pattern: **the apparatus generates critique and design faster than anything reconciles it against reality.** That, plus one genuinely hard unresolved decision (the rollback mechanism), is the stall.

3. **Scope is mostly right; the bloat is small and specific.** The portals are *not* placeholder pages (README.md:15 is wrong in the modest direction — a full Flask app with Pi-hole integration exists at `ansible/roles/portal/files/app/`). The real bloat: a `vpn:` CHOOSE field that nothing implements, two unused heavyweight dependencies (`weasyprint`, `pytest-testinfra`), and a DESIGN.md that promises ~15 commands and subsystems that were never built.

The path to v1.0 is roughly: one hard decision (restore strategy) + one hardware-validation session + a documentation truth-reconciliation pass + a handful of already-specced UX fixes. The details are in Part 4.

---

## Part 1 — Scaffolding Review

### 1.1 The persona rename fracture

`docs/personas.md` defines five installers (Priya, Dale, Tariq, Renata, Yemi) and four household members (Kwame, Seren, Aiko, Clem). But:

- **All 25 files in `docs/enhancements/`** attribute their findings to Alex, Morgan, Sam, Jordan, and Riley — a cast that no longer exists. ENH-009 is literally titled "audience framing excludes Morgan."
- **`.claude/agents/lankit-ux.md` still embeds the old cast** as its seed ("If `docs/personas.md` doesn't exist yet, seed it with these five: Alex… Morgan… Sam… Jordan… Riley") and its filing template says `**Persona(s):** Alex, Morgan`. The agent whose job is persona consistency carries a contradictory persona store inside its own definition.
- **GitHub issues ENH-026–035** (#44–#53) use the *new* household cast (Kwame/Seren/Aiko/Clem). So the rename happened around the portal work, the new pipeline was re-keyed, and the installer-side backlog never was.

The mapping is close to 1:1 (Alex→Priya, Morgan→Dale, Sam→Tariq, Jordan→Renata, Riley→Yemi), so no analysis was lost — but anyone (human or agent) reading an enhancement doc today has to reverse-engineer the mapping, and the UX agent, if re-run cold, could re-seed the old cast.

### 1.2 The unresolved audience tension (this one is substantive)

`docs/mission.md:21` says the target person "has a technical background… they write code, or they work in IT." `docs/mission.md:34` says "It's not for people who want a GUI." But `docs/personas.md:36-52` defines Dale — "not comfortable at the terminal… stops cold when anything goes off-script" — as a first-class installer persona, and ENH-009 (open as #17) argues DESIGN.md should drop "technical people" from its framing.

These pull in opposite directions, and it matters operationally: every enhancement filed on Dale's behalf (ENH-003, 006, 011, 022 — all "explain more, assume less") is contestable under the mission doc, and every mission-doc appeal to keep things lean is contestable under the personas. Unresolved, this makes prioritization arguments unwinnable in both directions — a plausible stall contributor. **This was a decision only you could make** (made 2026-07-06 — see Part 4, Gate 0.2).

### 1.3 DESIGN.md has become historical fiction

DESIGN.md is the largest single artifact and the most drifted. Specifics, checked against the code:

| DESIGN.md says | Reality |
|---|---|
| Comment convention is `kit:<unit>:<scope>:<resource>` (lines 776–809) | Live convention is `lankit:` (issue #43, closed 2026-05-17; e.g. `03-firewall.rsc.j2:38`, `probe.py:217`). DESIGN.md never updated. |
| CLI includes `lankit add / move / identify / rule / status / test / restore --dry-run / rollback --list / rollback --to N` (lines 819–888) | None of these exist. Actual command set is the README table (`lankit/cli/__main__.py:41-63`). |
| `lankit extend` = "reset the failsafe countdown" (line 883) | `extend` is the add-a-segment wizard (`extend.py:9`). Nothing named extend touches the failsafe. |
| Core library: `routeros.py, pihole.py, registry.py` + `web/` FastAPI portals (lines 926–954, 1183–1250) | Core is `config / generator / router / snapshots / passwords`; `lankit/web/__init__.py` is empty; portals are Flask under `ansible/roles/portal/files/app/`, deployed by Ansible, per `docs/apps/architecture.md`. The "Interface Contract" section describes an abandoned architecture. |
| Generated outputs include `RULES.md`, `TEST-MATRIX.md`, and a generated testinfra firewall suite (lines 531–654) | None exist. Their *function* was absorbed by `lankit rules / explain / matrix` (a defensible replacement — but undocumented as such). `tests/` contains Playwright portal tests only. |
| `test-failsafe` adds a DNS entry, user verifies with dig, PATH A/B (lines 1109–1141) | Implementation uses a disabled firewall sentinel rule and five automated phases (`test_failsafe.py:17-29`). |
| `failsafe_seconds` default 120 (line 1075) | `network.yml.dist:393` ships 300. |
| `lankit discover --unknown / --vendor / --segment / --update-vendors`, `lankit identify` (lines 697–772) | `discover` has `--new` and `--router` only (`discover.py:6-12`). |
| `lankit pki --share`, `lankit aws-setup` (lines 91, 125) | Don't exist (TLS/mkcert work deliberately deferred). |
| Rollback card contains WiFi password blanks, Option A/B structure (lines 976–1010) | Actual card (`rollback_card.py:40-114`) has a different three-option structure and no WiFi section at all (that's ENH-016). |

> **G2.1 note (2026-07-26):** the mechanical truth pass for this table landed in DESIGN.md — see the commit closing G2.1 below. All rows above except the rollback-card/failsafe/test-failsafe rows (deferred to G2.2, in-flight safety code) are now corrected in DESIGN.md itself; this table is kept as-is for the historical record of what was found.

Individually each is minor. Collectively, DESIGN.md fails its own job description ("the single architecture reference" per `.claude/agents/lankit-doc.md`): a contributor like Tariq — the persona who "reads DESIGN.md before README" — would be materially misled on his first hour in the repo.

### 1.4 TESTING.md and network.yml.dist contain stale *safety* claims

Doc drift is usually cosmetic. Two instances here are not:

- **`network.yml.dist:388-391`** (the comment above `failsafe_seconds`): "lankit provision and lankit restore apply changes in RouterOS safe mode. If you don't run lankit commit within this window, the router reverts." Three errors in two sentences: it's `apply` (not provision/restore) that's protected; the mechanism is explicitly *not* RouterOS safe mode (DESIGN.md:1056 makes this exact distinction); and `lankit commit` does not disarm anything — the apply confirmation prompt does (`commit.py` just saves a snapshot). A user who trusts this comment believes running `commit` is the keep-step and may watch their config auto-revert while holding a fresh snapshot.
- **`TESTING.md:119-125`**: says `extend` uses `yaml.dump` and may destroy the file (fixed — `extend.py:131-152` uses ruamel round-trip), says apply uploads to `/tmp/` (it uploads to flash root, `apply.py:127`), and still references `lankit status` (renamed to `overview`). TESTING.md:129 still has an "After testing — before creating the repo" checklist, i.e., a pre-publication artifact in a published repo.

### 1.5 The agent files

- **`lankit-ux.md`** — the old-persona seed (§1.1); also directs enhancements to `docs/enhancements/` while the live queue moved to GitHub issues, institutionalizing the two-store split (ENH-001–025 exist as files *and* issues; ENH-026–035 exist as issues only).
- **`lankit-auditor.md`** — references `lankit status` twice and a `kit/` working directory; its finding taxonomy (OK/ROGUE/MISSING/KNOWN-HARMLESS) predates `audit`'s actual ok/missing/drifted/rogue.
- **`lankit-reviewer.md`** — project layout points at `lankit/cli/templates/*.j2` (templates live in `ansible/roles/router/templates/`), and it runs on **haiku** — the smallest model reviewing the most safety-critical code in the project. The failsafe bugs (#6–#11) were exactly the class of subtle-correctness issue a small model is most likely to miss.
- **`lankit-triage.md`** — pins issue numbers in its prompt ("Failsafe bugs (#6–#11)… Agent issues (#38, #39)") that are partially closed; static issue references in agent prompts rot by construction.
- **`lankit-mission.md`** — references `lankit status`; otherwise consistent with `docs/mission.md`.
- **`lankit-doc.md`, `lankit-voice.md`, `mikrotik-docs.md`** — fine, modulo the `kit/` path and DESIGN.md-as-truth assumptions above.

### 1.6 Verdict: did the scaffolding cause the stall?

Partly — but not because it was a bad idea. The evidence says it worked as designed *as a critique generator* and lacked a closing loop:

1. **Output outran absorption.** 25 enhancement files + 10 portal UX-test issues + a full test-harness design (`docs/test-harness.md`, stranded on the unmerged `test-harness` branch, never implemented) + four portal specs, against a codebase that got ~9 commits since initial release. 50 open issues for ~4,700 lines of Python is a backlog that *feels* like a stalled project even where the code is fine.
2. **No reconciliation step.** Fixes land without closing paperwork (ENH-004 fixed by ruamel, #14 open; ENH-019 fixed by the re-arm commit, #26 open; ENH-001/018/024 fixed in the README, files unchanged). Renames land without propagation (personas, `kit:`→`lankit:`). Each agent tends its lane; nothing reconciles the lanes. "Living artifacts" became append-only artifacts.
3. **The one blocking question was un-delegatable.** The restore-mechanism problem (Part 2, S1) is an architecture decision with hardware-risk tradeoffs. Agents filed everything *around* it (#9, #10, #11 are its fringes) but the center — "is additive import a real rollback?" — needed you, and it was deferred. Projects stall at exactly the decision everyone routes around.
4. **Surface expansion beat core hardening.** The household personas are excellent design work, but they pulled effort toward portals and Playwright specs while installer-side safety items (ENH-002, -005, -012 — all filed as High in April) sat. The portals then created their own 10-issue queue.

The scaffolding is worth keeping. It needs a sync ritual (Gate 2) and a rule that renames propagate the day they happen.

---

## Part 2 — Safety-Bar Audit

Bar: *genuinely hard for a household to end up offline with no way back.* Findings ranked. S1–S4 are v1.0 gates; the rest ship-with-notes.

### What's already strong (state this plainly, because the queue obscures it)

- The failsafe scheduler survived a real hardening pass: self-deleting one-shot on-event (`apply.py:132-138`), midnight/month boundary handling (`router.py:85-120`), cancel-with-readback-confirmation (`router.py:167-176`), re-arm before the confirm prompt so the user gets the full window (`apply.py:187-199`, closing ENH-019), Ctrl+C handling (`apply.py:205-218`), and import-error fail-stop with automatic restore (`apply.py:177-184`, most of ENH-025).
- Config validation is real: JSON Schema + cross-reference checks + CHOOSE-field guidance (`config.py:185-274`); unfilled CHOOSE values fail at `overview` time, not apply time (largely defusing ENH-007).
- Templates are idempotent and segment-scoped (`03-firewall.rsc.j2:15-29`), so re-apply is safe and `--segment` reverts can't nuke other segments.
- `probe` and `audit` are genuine verification tools — expected-vs-live tag comparison, gateway pings, drift/rogue classification (`probe.py`, `audit.py`).
- `.gitignore` correctly excludes every sensitive artifact (`network.yml`, `wifi-vault.yml`, both cards, `certs/`, generated output).

### S1 — The restore path can't undo an apply (GATE; not in any queue)

Every automatic recovery mechanism reduces to importing a `/export verbose` snapshot over the live config:

- Failsafe: `on-event = /import file=lankit-restore.rsc` (`apply.py:135-138`)
- Decline-at-prompt and import-error paths: `_restore_snapshot()` → `/import` (`apply.py:256-268`)
- `lankit rollback`: upload + `/import` (`rollback.py:58-62`)
- `lankit restore`: upload + `/import` (`restore.py:97-101`)

RouterOS `/import` is **additive** and **halts at the first error**. Against a config that a bad apply just *added rules to*, the snapshot's `add` commands hit duplicates almost immediately; the import aborts; the bad rules stay. The scenario the failsafe exists for — apply pushed a connectivity-killing rule, SSH died, scheduler fires — is the scenario where import-over-live-config is least likely to work. (Import-after-factory-reset — rollback card Option 1→2 — is the one case where it mostly *does* work, because the config is near-empty.)

Compounding it, **all three CLI restore paths swallow the failure**: stderr from the import is printed as a yellow `Warning:` and the command then prints a green `✓ Rolled back` / `✓ Restored` (`rollback.py:60-64`, `restore.py:99-103`, `apply.py:266-268`). Issue #9 ("snapshot restore failure during rollback is silent") is the only queue item that grazes this, and it frames it as a messaging bug. It isn't. It's the mechanism.

And the drill certifies the wrong thing: **`test-failsafe`'s revert command is a targeted `/ip firewall filter remove [find comment=...]`** (`test_failsafe.py:53-54, 85-89`) — it proves the scheduler fires, never that a snapshot import restores anything. A user who runs the drill, sees `✓ PASS — Scheduler-based failsafe is working correctly`, and trusts the failsafe on a live apply has been given false assurance on exactly the property the mission doc calls non-negotiable (`docs/mission.md:66`).

Options, in rough order of my preference:

1. **Tag-scoped revert script (recommended).** lankit owns every resource it creates via `lankit:` comments — that's the stated point of the convention (DESIGN.md:806, "the comment is the contract"). A revert script can therefore be *generated*: remove everything matching `lankit:*`, then re-add from the pre-apply snapshot's lankit-tagged subset (or simply re-run the last-known-good generated scripts, which are already remove-then-add idempotent). This works over a live config, doesn't fight defconf, and needs no reset.
2. **Reset-based restore** (`/system reset-configuration run-after-reset=…`) — the deferred decision. Strongest guarantee, most disruptive, scariest to fire automatically on a timer.
3. **Minimum honest v1.0:** keep additive import for the factory-reset path only; make every restore path verify (re-export, compare, or at least check import stderr) and **fail loudly**; rewrite the rollback card and docs to say exactly what rollback can and cannot do; make `test-failsafe` exercise the real import path.

Whichever you choose, the choice must be validated on hardware with a deliberately broken apply before v1.0 (Gate 1 below). The unmerged `test-harness` branch's design doc already describes the lab setup for exactly this.

### S2 — Running `rollback` twice re-applies the config you just reverted (GATE; not filed)

`rollback` restores `snapshots.previous()` = `snaps[-2]` (`rollback.py:35`, `snapshots.py:82-87`). Trace it: after an apply the stack ends `…, pre-apply, post-apply`. First rollback: `snaps[-2]` = pre-apply ✓ — but it first saves the current (bad) config as `pre-rollback`, so the stack now ends `…, pre-apply, post-apply, pre-rollback`. Second rollback: `snaps[-2]` = **post-apply — the bad config**. The user who runs rollback twice because the first one "didn't seem to work" (see S1 — it plausibly didn't) oscillates between broken states. Yemi-under-stress and Clem-with-the-fridge-card are exactly the people who will do this. ENH-008 (rollback shows a filename and nothing else, #16) makes it worse: nothing on screen would reveal the oscillation.

Fix is small: rollback should walk *backward through pre-apply-labeled snapshots* (labels are already parsed — `snapshots.py:53-65`), skip its own `pre-rollback` markers, and show what ENH-008 asks for (age, label meaning) before the prompt.

**Resolved 2026-07-27 (G1.5, 43c9220)** — jointly with G1.4's convergence mechanism, exactly as design §8 anticipated: `snapshots.select_rollback_target()` does the backward walk (depth read off the `pre-rollback` markers themselves), and the pre-prompt context block makes the direction of travel visible on screen.

### S3 — Silent-failure residue in the recovery chain (GATE)

Beyond S1's swallowed import errors: apply's error heuristic (`apply.py:156-175`) is string-pattern matching (`"failure"`, `"error"`, … vs `"warning"`) — fragile by its own admission, and ENH-025's remaining asks (consolidated error summary, `apply.log`, `--strict`) are still open (#32). Snapshot rotation silently discards the oldest at 10 (`snapshots.py:20,103`) with two snapshots consumed per apply — five applies of history, no warning, no pin (ENH-023/#30). These are all one-session fixes; together they close #9, #30, #32.

### S4 — Apply disarms the failsafe with zero verification (GATE; already specced as ENH-002/#1)

After "Keep these changes? y", the failsafe is cancelled with no check that the network works (`apply.py:220-234`). The verification machinery *already exists* — `probe`'s gateway pings and tag audit (`probe.py:139-170`). Wiring a fast subset (gateway pings; optionally DNS reachability) between "y" and the cancel, with a "some gateways unreachable — keep anyway?" re-prompt, converts the failsafe window from a trust exercise into a checked handoff. This is the single highest-leverage cheap change in the queue, and it's been specced since April.

### Ship-with-notes (real, but not gates)

- **S5 — No pre-apply diff** (ENH-005/#3, ENH-014/#21): `--dry-run` + `rules` + `audit` cover most of the need; a true `--diff` and a `health` command are good post-1.0 items (probe is already 80% of `health`).
- **S6 — Manual step buried in a generated file**: `03-firewall.rsc.j2:138-139` — "MANUAL STEP: disable the defconf fasttrack rule after importing." A manual step in generated output contradicts the no-manual-drift non-negotiable (`docs/mission.md:70`), and nothing surfaces it at apply time. Enforcement of *new* connections is unaffected (default-deny matches `connection-state=new`), so it's a correctness footnote rather than a hole — but either script the disable or have `audit` flag it.
- **S7 — Raw SSH errors** (ENH-012/#5): classification + checklists, exactly as specced. Matters most in the recovery moments (Yemi after the firmware update).
- **S8 — `--segment` before global rules** (ENH-013/#20): the specced default-deny presence check is right; cheap.
- **S9 — Rollback card gaps** (ENH-016/#23): no SSIDs, no Pi-hole URL; card Option 2 also tells the user to run `lankit restore` — whose reliability is S1. Card copy must be revised as part of whatever S1 lands.

---

## Part 3 — Scope Cut

### Keep in v1.0 (built, aligned, earn their place)

Core pipeline (`overview/generate/apply/probe/audit/rollback/restore/commit/snapshots`), the discover wizard, `explain/matrix/rules/diagram`, both cards, `extend`, `secrets`, dns-server provisioning, `reset-provision`, `test-failsafe` (after S1 makes it drill the real revert).

### The portals: keep as explicitly-experimental, don't let them gate v1.0

README.md:15 calls them "currently placeholder pages." False — there's a complete Flask app (bypass scheduling with APScheduler crash-recovery, Pi-hole v6 API client, device rename/registration, speedtest/latency crons, systemd unit, Caddy vhosts) under `ansible/roles/portal/files/app/`. The household personas' resilience scenarios (issues #44–#53) all run through these portals — they're load-bearing for the persona story.

But they're also the newest, least-tested surface, they're already optional (`app_server.enabled: false` by default, `network.yml.dist:345`), and their 10-issue test queue would push v1.0 out by months. Recommendation: **v1.0 ships them as "beta, optional, off by default" with an honest README paragraph**; the persona-resilience test suite (#44–#53) is the headline of v1.1. Cutting them from the repo would discard real, working value for tidiness.

### Cut or fix before v1.0 (the actual bloat)

| Item | Evidence | Action |
|---|---|---|
| `vpn:` CHOOSE field | `network.yml.dist:301` forces a wireguard/ipsec/none decision; no template or command implements any of it (grep: only `config.py` loads it, `discover.py:268` writes `none`); #12 open | Mark "not yet implemented — choose none" in dist + schema, or drop the field until WireGuard lands. A CHOOSE that does nothing is a broken promise at the tool's most sacred surface. |
| `weasyprint` dependency | `pyproject.toml:22` ("rollback card PDF generation"); rollback card is plain text (`rollback_card.py:117`); zero imports | Remove. It drags cairo/pango system libs into every install. |
| `pytest-testinfra` dependency | `pyproject.toml:18`, runtime dep; zero uses in repo; #33 open | Remove (re-add as dev dep if the testinfra suite is ever built). |
| DESIGN.md aspirational subsystems | device registry, `add/move/identify/rule`, FastAPI web/, aws-setup, pki --share, generated testinfra suite | Delete from DESIGN.md or move to an explicit "not built / maybe never" section. This is scope-cutting by documentation, and it's most of what makes the project *look* unfinished. |
| `lankit/web/` package | empty `__init__.py` | Delete the directory. |
| Stale branches | `origin/fix/failsafe-hardening`, `origin/fix/idempotent-templates` (both superseded by main), local+remote `test-harness` (only unique content: `docs/test-harness.md`) | Extract `docs/test-harness.md` to main; delete all three. |

### Explicitly post-1.0

WireGuard (#12), second MikroTik / wired-AP / hardware matrix (#35–#37), mDNS/AirPlay (#54–#55), `community.routeros` migration question (#56), `health` command (#21), `apply --diff` (#3), devil's-advocate agent (#38), portal persona suite (#44–#53).

---

## Part 4 — Execution Plan

*Expanded 2026-07-06 after the Gate 0 decisions were made. Written to be run by an orchestrating session that delegates individual units to agents. Gates are thematic groupings; the actual execution order is the dependency edges and the wave schedule at the end — several Gate 2/3 units can run in parallel with Gate 1.*

### Gate 0 — Decisions (made 2026-07-06)

1. **Restore strategy: tag-scoped revert.** The automatic mechanism (failsafe, rollback, restore) removes/rebuilds by `lankit:` tag ownership rather than additive snapshot import. Additive import of a snapshot remains valid only for the post-factory-reset path on the rollback card.
2. **Audience: the installer is technical.** The installation bar is not softened. Dale-class users are served through lankit's *outputs* (cards, plain-English rules, portals) and recovery paths, not by redesigning setup. ENH-009/#17 resolves by fixing DESIGN.md's opening line to *describe* the audience rather than gatekeep, without changing who the audience is.
3. **Portals: beta, optional, off by default** (matching `hosts.app_server.enabled: false` in the dist). README reframed from "placeholder pages" to an honest description of what exists, labeled experimental. The persona test suite (#44–#53) is v1.1.

Three low-stakes defaults adopted during planning (veto by telling the orchestrator; each is reversible):

- **`vpn:` field** — kept in schema/dist but comment-marked "not yet implemented — choose `none`" (removing the field would break existing configs against the schema's `required` list). #12 gets a note, stays open as the implementation ticket.
- **Enhancement store** — GitHub issues become the single live queue; `docs/enhancements/` is frozen as an archive with a banner line, not deleted.
- **`lankit-reviewer` model** — promoted haiku → sonnet (unit R5).

### Conventions for the unit tables

- **Owner** — `GP` = fresh general-purpose agent; a named persona means the `.claude/agents/*` agent, **usable only after its repair unit (Rn) has landed** — no persona executes its own repair; **JASON** = cannot be delegated at all.
- **Depends** — hard prerequisites. "apply-queue" marks units that all edit `lankit/cli/commands/apply.py` and must land serially in the order given, regardless of other parallelism.
- **Closes** — GitHub issue(s) the unit should close (with a comment citing the commit).
- **Standing rule for every delegated unit:** no agent runs commands against the production router (192.168.88.1 / live network) at any point in Gates R–4. All development is offline (unit tests, generated-file assertions, mocked connections). Anything that needs live RouterOS behavior is deferred to G1.13, which is Jason's. Read-only lab-router access (once the lab exists) is permissible if Jason exports `LANKIT_CONFIG=lab-network.yml` for the session.

### Gate R — Toolchain repair (all GP, all independent, all parallel-safe)

These exist because six of the nine agent personas are stale, and per the ground rule a persona may not repair itself. Each is a small, single-file edit to `.claude/agents/<name>.md`. R-units block only the persona-owned units listed in their "Unblocks" column — everything else can proceed without them.

| Unit | File | What to fix | Unblocks |
|---|---|---|---|
| R1 | `lankit-ux.md` | Delete the embedded Alex/Morgan/Sam/Jordan/Riley seed block (lines ~457–495); replace with "load `docs/personas.md`; if missing, stop and report rather than seeding." Fix the filing template's `Persona(s): Alex, Morgan` example to use current names. Change the filing target: enhancements are filed as GitHub issues (`gh issue create`), `docs/enhancements/` is a frozen archive. | G2.4 review, any future UX work |
| R2 | `lankit-auditor.md` | `lankit status` → `lankit overview` (2×); drop "kit/ directory" phrasing; align finding taxonomy with audit's actual ok/missing/drifted/rogue. | G1.12 |
| R3 | `lankit-mission.md` | `lankit status` → `lankit overview`; add the Gate 0.2 audience resolution to its framing notes so it doesn't relitigate. | G2.7 |
| R4 | `lankit-triage.md` | Remove pinned issue numbers (#6–#11, #38, #39 notes); replace with instructions to derive clusters from live `gh issue list` output each run. | post-1.0 triage runs |
| R5 | `lankit-reviewer.md` | `model: haiku` → `model: sonnet`; fix layout (templates live in `ansible/roles/router/templates/`, not `lankit/cli/templates/`; core is config/generator/router/snapshots/passwords); note the Flask portal path as reviewable surface. | G1.11, G3.9 |
| R6 | `lankit-doc.md` | Drop `kit/` root; update the project-structure block to reality (docs/ contents, portal location); soften "DESIGN.md is the single architecture reference" to "keep DESIGN.md truthful — flag drift when found." | G2.1, G2.2 |

### Gate 1 — Safety (the hard gate)

| Unit | What | Owner | Depends | Closes |
|---|---|---|---|---|
| G1.0 | ✅ **Done** (6dcec4b). Extract `docs/test-harness.md` from the `test-harness` branch onto main. Branch deletion itself waits for G4.3. | GP | — | — |
| G1.1 | ✅ **Done** (18a47b0, amended 03a5226). Revert-mechanism design note — `docs/design/tag-scoped-revert.md`. | GP (+ mikrotik-docs as sub-consult) | — | — |
| G1.1a | ✅ **Done, signed off 2026-07-07** (03a5226) with four amendments (durable failsafe-fired marker beyond the `/log` scan; archive-retention sweep enforcement; probe.py migration pulled into G1.2's scope; `test-failsafe`'s real-bundle drill gated behind `--full`, default stays zero-churn). | **JASON** | G1.1 | — |
| G1.2 | ✅ **Done** (1e665ed, 2026-07-25). `lankit/core/revert.py`: TAG_TABLES registry (verified against all 7 templates), build_remove_pass, known-good archive store + enforced retention sweep (snapshots.py index now carries archive_id pairing), build_revert_bundle/expected_tags/verify_revert, generator.py's render_texts()+multi-segment segment_filter, probe.py migrated onto the shared registry. 96 tests. **Found along the way:** probe.py's old expected-tag list and live-tag parser were both wrong on real hardware (missing tables; assumed an inline `comment="..."` format RouterOS 7 doesn't use — fixed by porting audit.py's proven `;;; text`-line parser). Also documented, not fixed: segment-scoped renders can reference an address-list outside the render (intentional — matches the documented incremental-apply-order workflow; a self-contained render for the archive case is a future caller's job via `dataclasses.replace`). | GP | G1.1a, G1.10 | — |
| G1.3 | ✅ **Done** (0bfab7b, 2026-07-25). `apply.py` rebuilt on `revert.py`: verified upload (exact byte size) before the scheduler installs; on-event imports `lankit-revert.rsc` and leaves the durable `lankit-failsafe-fired` marker on completion/failure (sign-off amendment), checked on every connect before anything else; decline/import-error paths import + verify convergence instead of the old unchecked `_restore_snapshot`; master-WiFi facts captured every apply; migration bootstrap for archive-less existing routers (design §9); known-good archive updated on keep via `_archive_update_plan` (full apply = new baseline, `--segment` = union re-render, `--script` = skipped). `RouterConnection` gained `read_lankit_tags`/`read_master_wifi_facts`/`verify_uploaded_file`; probe.py deduped onto the first. 23 new tests (pure helpers only — the live-router flow itself needs G1.13, not a mock). **Found and fixed in passing:** the import-error classifier didn't catch ENH-025's own real example RouterOS error text ("input does not match any value of interface") — added the missing pattern. | GP | G1.2 | part of #9 |
| G1.4 | ✅ **Done** (82d64b0, 2026-07-27). `rollback.py`/`restore.py` rebuilt on `revert.py`: target snapshot → paired known-good archive (§8), verified bundle upload, import over the live session, live-tag read-back vs the archive's expected set — green mark only on clean stderr AND `verify_revert` ok, otherwise bold red "did NOT complete" + evidence lists + exit 1 (§7.2). Unpaired snapshots (§9) get the honest-refusal path: explicitly-confirmed remove-only revert (never auto-accepted by `--yes`) or printed factory-reset + additive-import instructions. Shared flow lives in `_revert_shared.py` — `classify_import_error` and the connect-time failsafe-fired check extracted mechanically from apply.py, all three callers share them; every `snapshots.save` call site (pre-apply, pre-rollback, pre-restore, commit) now pairs to the archive current at save time. **Found along the way (G1.11 interim finding, folded in):** `sweep_orphaned_archives` could delete the *active* known-good archive — an unpaired migration-bootstrap archive died on the next index write, and ten routine unpaired commit/rollback/restore saves rotated the one paired post-apply snapshot out of the 10-cap and took the current archive (plaintext WiFi passphrases, the failsafe's revert target) with it. Fixed both ways: latest archive per router unconditionally sweep-protected + universal save-site pairing. Also: `/system scheduler` added to `TAG_TABLES` as a read-only row (verification sees lingering `lankit:failsafe` tags; remove-pass still matches by exact name only), `verify_revert` deliberately ignores the durable `lankit:failsafe-fired` marker, and failure evidence flags legacy `kit:` tags as pre-rename leftovers needing one-time manual cleanup. 146 tests + 1 xfailed (was 119). | GP | G1.2 | #9 |
| G1.5 | ✅ **Done** (43c9220, 2026-07-27). `snapshots.select_rollback_target()` replaces `snaps[-2]`: rollback walks backward through `pre-apply`-labeled snapshots (segment applies included), never onto `post-apply` or its own `pre-rollback`/`pre-restore` markers. Depth comes from state already on disk — the count of `pre-rollback` markers newer than the newest candidate — so no new state file: first rollback after an apply → that apply's pre-apply snapshot, second → the one before it, strictly older each time; a new apply resets depth to 0. Depth clamps at the oldest candidate and says so (design §8: landing twice on the same target is idempotent convergence, so staying put is honest, wrapping forward is not); routers with no `pre-apply` snapshots at all fall back to the same walk over non-operational snapshots rather than refusing. ENH-008 display (`_revert_shared.print_snapshot_context`, used by rollback **and** restore): age ("5 months ago" + absolute UTC), label + its plain-English meaning, size, and what the target actually restores (paired archive id / resource count / segments / globals, or an honest `unpaired (legacy)` / `archive missing` line); rollback adds a walk line when depth > 0. S2 is closed jointly with G1.4's convergence mechanism, as design §8 predicted. **Also done (G1.4 hand-off note):** `snapshots.repair_pairing()` — apply saved its pre-apply snapshot before the migration bootstrap ran, stranding it unpaired on a bootstrapped router even though the bootstrap archive describes exactly its state; apply now re-pairs it after a successful bootstrap (fills a missing pairing only, never overwrites; no change to apply's flow semantics). 183 tests + 1 xfailed (was 150), including the S2 trace as an explicit test. **Found along the way, not fixed (out of scope):** the same-second snapshot filename collision (#57, deferred post-1.0) is easy to hit in scripted/test sequences — two saves in the same second overwrite one file and leave two index entries pointing at it. | GP | G1.4 (same files) | #16 |
| G1.6 | ✅ **Done** (dcde5c2, 2026-07-27). Post-apply health check runs between "Keep? y" and the disarm, so a failed check can still be declined into the revert path instead of kept-then-rolled-back. Gateway pings for every applied segment (reusing probe's `_ping_gateway`); `--segment` mode checks only that segment's gateway, since an incremental build-out legitimately has the others not up yet. **DNS is a real lookup through the configured resolver, not a ping to it** — the 2026-07-20 outage was a hung Pi-hole FTL process that answered ICMP the whole time while every query timed out, so a ping-only check would have called it healthy. Only checked when `hosts.dns_server` exists (a public `dns_ip` is not something an apply can break). On failure: evidence lines, the failsafe re-armed to a full window (probing spends part of it), and a second prompt defaulting to no. `--skip-probe` disarms immediately; `--dry-run` never reaches this path. Ctrl-C handling shared across both keep-prompts so neither can leave a live failsafe behind unannounced. 11 new tests (194, was 183). | GP | G1.3 | #1 |
| G1.7 | ✅ **Done** (cb96bea, 2026-07-27). G1.3 had already made *classified* import errors fatal; this closes the residue — unrecognised stderr that printed once mid-scroll and was forgotten by the time the user reached the keep-prompt. Consolidated per-script recap immediately before the prompt, which then defaults to **no** (walking away reverts rather than keeps). Every script's raw stdout/stderr appended to `ansible/generated/apply.log` with a per-run header — appended, not overwritten, because an incremental build-out is several applies and the sequence is usually the diagnosis; write failures are swallowed so logging can never break an otherwise-good apply. `--strict` treats any import stderr as fatal, opting out of the classifier's benefit of the doubt (the thing ENH-025 distrusts) and taking the same cancel-and-revert path. 6 new tests (200, was 194). | GP | G1.6 | #32 |
| G1.8 | ✅ **Done** (daa689b, 2026-07-27). Index entries carry a `pinned` flag (legacy entries read as unpinned). `_rotate()` keeps every pinned entry and trims to the newest N unpinned, so the cap bounds *automatic* history only — a pin raises the total rather than evicting something else, and a router with ten pins still can't rotate out the pre-apply snapshot the current run just wrote. `rotation_status()` reports headroom, the oldest survivor, and the next casualty. `lankit snapshots --pin/--unpin` by index or `latest`, plus Age and Pin columns and a footer naming the next casualty with the exact command; the File column went (it was `<router>-<timestamp>-<label>.rsc`, derivable from columns already shown, and was truncating everything else to ellipses). `apply` warns *before* saving its pre-apply snapshot, so the snapshot it names is still there to be pinned. `rollback` states how far back history reaches and that its own pre-rollback snapshot is one of the things rotating. Pinning guards against the automatic sweep, not against `--delete`. 14 new tests (214, was 200). | GP | G1.7 (for the apply.py touch only; snapshots.py work can start anytime) | #30 |
| G1.9 | ✅ **Done** (adbc5a1, 2026-07-27), per design §12's amendment (default/`--full` split). `build_failsafe_on_event()` extracted from apply.py into `_revert_shared` and used by both, so the drill installs production's on-event **byte for byte** — only the imported filename differs, and a test pins that equivalence (a rehearsal running a different script than production proves less than it appears to). The sentinel is a genuinely `lankit:`-tagged filter rule (`lankit:test:sentinel`), so in `--full` the production remove-pass deletes it for the real reason — it matches `^lankit:` in a TAG_TABLES table. Default mode stays zero-churn (imports `lankit-drill.rsc`, one command, no VLAN/DHCP/WiFi/firewall state touched). `--full` uploads and fires the actual bundle, then asserts state was **rebuilt**, not merely removed (`verify_revert` against the archive's expected tags); gated on an archive existing plus an explicit confirmation naming the churn. Phase 5 now checks four things instead of one — sentinel gone, scheduler self-deleted, durable fired-marker present and `ok`, log markers as corroboration only — and reports failures together. **Found along the way:** pre-flight didn't clear a stale fired-marker, which would have made Phase 5 pass for the wrong reason on a second run; it does now. 14 new tests (234, was 221). | GP | G1.3 | — |
| G1.10 | ✅ **Done** (56dc18f). Unit-test bootstrap: `tests/unit/` config/generator/snapshots tests, `dev` extra, CI workflow. Playwright suite untouched. Later extended by G1.2's own commit (test_revert.py + generator/snapshots additions). | GP | G1.0 | — |
| G1.11 | **Reviewer pass** over all Gate 1 diffs (correctness, RouterOS syntax, silent-failure hunting). Findings loop back as fix-ups before G1.13. | lankit-reviewer | R5, G1.3–G1.9 | — |
| G1.12 | **Hardware-validation runbook.** A printable checklist mapping each break scenario to the code path it exercises: mid-apply SSH kill (failsafe fires → revert), bad script (fail-stop → revert), decline at prompt, double rollback (must go further back), first-apply revert, restore-over-live. Includes expected observations and the exact commands. | lankit-auditor | R2, G1.3–G1.5, G1.9 | — |
| G1.13 | **HARDWARE VALIDATION SESSION.** Lab router per `docs/test-harness.md` (or CHR), lab `LANKIT_CONFIG`, run the G1.12 runbook, deliberately break a working config every way listed, confirm every path recovers. Only after a full lab pass does anything touch the production router. **This is the v1.0 gate.** | **JASON** (agents may assist interpretation) | G1.12 | — |

### Gate 2 — Truth reconciliation

| Unit | What | Owner | Depends | Closes |
|---|---|---|---|---|
| G2.1 | ✅ **Done** (2026-07-26). DESIGN.md mechanical truth pass (everything *except* the safety sections): `kit:` → `lankit:` convention; replaced the fictional CLI section with the real command set (grouped by setup/router/safety/growing-the-network, with a "Not real commands" pointer); fixed file-structure/architecture sections (Flask portal path, real `lankit/core/` modules, deleted the fictional FastAPI "Interface Contract" section); real `discover` flags (`--new`/`--router` only); corrected `extend` (segment wizard, not failsafe) and `restore` semantics; added a consolidated "Future / not built" section (device registry, add/move/identify/rule, aws-setup, pki --share, generated testinfra suite, RULES.md/TEST-MATRIX.md, vpn: field, discover's fictional flags) noting where function was absorbed by real commands (rules/explain/matrix/probe/audit); corrected the Registration Portal section after checking `app.py` (it writes a Pi-hole DNS entry + client comment only — no static lease/segment assignment, contrary to the earlier draft); fixed the diagram-flags fiction (`--svg`/`--dot`/`--ascii`/ASCII fallback don't exist) found in passing. Audience opening line: the Gate 0.2 rewrite had already landed on main (b4370c0) and is preserved. Safety/failsafe/rollback sections deliberately left untouched (G2.2's scope, in-flight code). | lankit-doc | R6 (audience line also G2.7 coordination) | #28 partial |
| G2.2 | ✅ **Done** (206e390, 2026-07-28). Safety-surface copy rewritten for the tag-scoped revert: DESIGN.md's Rollback & Recovery section (failsafe, rollback card, `lankit rollback`/`restore`, test-failsafe drill — all deliberately left untouched by G2.1) now describes `lankit-revert.rsc`, convergence-not-undo, the wired `.88` survival path, the durable fired-marker, and the real 5-phase test-failsafe drill (`--full` noted as designed-but-not-yet-implemented, G1.9); `network.yml.dist`'s Safety comment fixed (apply, not provision/restore; not RouterOS safe mode; the keep-prompt disarms, not `lankit commit`); TESTING.md Phase 4 aligned with the real revert bundle and drill. `rollback_card.py` Options 2/3 rewritten for the new mechanism (`lankit rollback` = verified convergence; manual re-run = `/import file=lankit-revert.rsc`), Option 1 (factory reset) keeps the distinct `lankit-restore.rsc` additive-import filename per design §6. Closes ENH-016 (#23): WIFI NETWORKS section (SSIDs, references the password card for credentials), SERVICES section (Pi-hole admin URL when configured), regenerate-both-cards reminder. 150 tests + 1 xfailed, unchanged. | lankit-doc (card code edit sub-delegated to GP) | R6, G1.3, G1.4 | #23 |
| G2.3 | ✅ **Done** (9b9296f). TESTING.md non-safety fixes: `status`→`overview`; stale extend/yaml.dump warning and the `/tmp` claim deleted; "before creating the repo" checklist removed. | GP | — | — |
| G2.4 | ✅ **Done** (e9a5e3f). Persona re-key across `docs/enhancements/*` (Alex→Priya, Morgan→Dale, Sam→Tariq, Jordan→Renata, Riley→Yemi) + archive banner freezing the directory (live queue is GitHub issues). | GP, review by lankit-ux | review leg: R1 | — |
| G2.5 | **Issue hygiene:** close #13, #14, #25, #26, #31 with commit-citing comments; verify-and-close or annotate #15 (ENH-007, mostly fixed) and #24 (ENH-017, partial); comment on #12 recording the "marked not-implemented for v1.0" default; #33 closes via G3.8. | GP (gh auth already present locally) | — | #13 #14 #25 #26 #31 (+#15/#24 judgment) |
| G2.6 | **README pass** (single unit — the file is small and these collide): portal paragraph rewritten per Gate 0.3 (beta/optional/off-by-default, what actually exists); quick-start step to inspect `ansible/generated/*.rsc` between generate and apply (ENH-021); `python --version` check note (ENH-018 residue). | lankit-voice | — | #28, finishes #25 |
| G2.7 | **Mission/audience closure:** one-paragraph addition to `docs/mission.md` recording the Gate 0.2 resolution (installer technical; household served via outputs) + revision-history line; confirm DESIGN.md opening line (landed via G2.1) satisfies it; close #17 with a comment stating the resolution. | lankit-mission | R3 (G2.1 for the DESIGN.md line) | #17 |

### Gate 3 — Onboarding polish (all independent unless noted)

| Unit | What | Owner | Depends | Closes |
|---|---|---|---|---|
| G3.1 | ✅ **Done** (7fa50e4). ENH-003: missing-config error in `config.py` names all three recovery paths — `cp network.yml.dist network.yml`, `lankit discover --new`, `LANKIT_CONFIG` — and only offers the template line when `network.yml.dist` is actually present. Row marked complete 2026-07-27 after verifying the shipped code, not just the commit subject. | GP | — | #2 |
| G3.2 | ✅ **Done** (e522db0). ENH-012: `RouterConnection._classify_connect_error` sorts connect failures into auth / no-route / refused-or-timeout, each with its own diagnosis checklist, and falls back to the generic message on anything unrecognised. Shared by every caller through the connection itself. Row marked complete 2026-07-27 after verifying the shipped code, not just the commit subject. | GP | — | #5 |
| G3.3 | ✅ **Done** (741cad7, 2026-07-27). Pre-flight before anything is written: read the firewall table and, if no default-deny tag is present, state plainly that segments are **not isolated from each other** and internet access may not work yet, then confirm (default yes). Matched on the tag suffix so a pre-rename `kit:` router reads as configured; an unreadable table reads as "couldn't tell", never as "absent". After a kept segment apply: which segments remain, the exact command for the next one, and the full apply that finally enforces isolation. Recommended order moved from a buried sentence to a numbered block at the top of `--help`. `--yes` skips pre-flight confirmations only, never the keep/revert prompt — pinned by a test, since an unattended apply that could auto-keep a broken config would defeat the failsafe. **Deviation from the issue text, deliberate:** #20 proposed warning "run `lankit apply` first, then use `--segment` for incremental changes", which contradicts lankit's own documented order (segments first, full apply last) and would have fired on every step of the intended onboarding path. The warning states the true consequence instead of implying the user did it wrong. 7 new tests (221, was 214). | GP | G1.7 (apply-queue) | #20 |
| G3.4 | ✅ **Done** (84af272). ENH-020: `rules` prints the "run lankit generate first" pre-flight before any other output when `ansible/generated/` is empty or absent. **The row's premise about `diagram` was wrong:** `diagram` renders from `network.yml` directly and never reads `ansible/generated/`, so it has no such failure mode and needs no guard (verified 2026-07-27). Row marked complete 2026-07-27 after verifying the shipped code, not just the commit subject. | GP | — | #27 |
| G3.5 | ✅ **Done** (8d3762b). ENH-011: `network.yml.dist` explains `{{ household_name }}` expansion with a concrete example, immediately above the first templated SSID. Row marked complete 2026-07-27 after verifying the shipped code, not just the commit subject. | GP | — | #19 |
| G3.6 | ✅ **Done** (ce9c0be). ENH-015: the `extend` wizard asks which segments can reach the new one, validates the names, and writes them into `permissions:` — including appending the new segment to existing `can_reach` lists where the answer implies it. Row marked complete 2026-07-27 after verifying the shipped code, not just the commit subject. | GP | — | #22 |
| G3.7 | ✅ **Done** (1b66559, 2026-07-27). S6 fasttrack: the MANUAL STEP comment in `03-firewall.rsc.j2` is now a scripted, find-guarded in-place disable of the defconf fasttrack rule. Followed the approved revert design's constraint (§5.3/§11): the defconf rule is deliberately *not* tagged `lankit:` (a tag would make the remove-pass delete a defconf rule) — disabled in place with its defconf comment untouched, no-op when absent or already disabled, emitted only with `include_globals`. `apply.py` builds the revert bundle with `fasttrack_reenable=True`, so the remove-pass re-enables it (`enable [find where comment="defconf: fasttrack" && disabled=yes]` — already implemented in `revert.py` since G1.2). No audit/probe change needed: nothing referenced the manual step, and `audit` already checks the `lankit:fw:all:fasttrack-internet` replacement rule. 4 new tests. | GP | G1.1a | — |
| G3.8 | ✅ **Done** (cd7a72e + a9862c7). `weasyprint` and `pytest-testinfra` removed from `pyproject.toml` (a `dev` test extra added in their place); `vpn:` marked not-yet-implemented in the dist comment and the schema description. Row marked complete 2026-07-27 after verifying the shipped code, not just the commit subject. | GP | — | #33, updates #12 |
| G3.9 | Reviewer pass over Gate 3 diffs. | lankit-reviewer | R5, G3.1–G3.8 | — |

### Gate 4 — Release mechanics (strictly after G1.13 passes)

| Unit | What | Owner | Depends | Closes |
|---|---|---|---|---|
| G4.1 | Version bump 0.1.0 → 1.0.0 in `pyproject.toml` + `lankit/__init__.py`. | GP | G1.13, all G2/G3 landed | — |
| G4.2 | CHANGELOG / release notes in lankit-voice's humanized format ("Provision now confirms…" style), covering everything since `1df0c54`. | lankit-voice | G4.1 | — |
| G4.3 | Pre-push hygiene: sensitive-data scan, commit-message audit, branch-base check per the agent's own checklist. | lankit-git | G4.2 | — |
| G4.4 | **Push main, delete stale branches** (`origin/fix/failsafe-hardening`, `origin/fix/idempotent-templates`, `test-harness` local+remote — after confirming G1.0 landed), **tag v1.0.0**, publish release notes. Outward-facing and destructive-adjacent: Jason executes or explicitly green-lights each push/delete. | **JASON** (lankit-git prepares the exact commands) | G4.3 | — |

### Dependency graph (summary)

```
R1 R2 R3 R4 R5 R6          G1.0        G1.1            G2.3 G2.5 G2.6  G3.1 G3.2 G3.4 G3.5 G3.6 G3.8
 │  │  │     │  │            │           │                      (independent — start anytime)
 │  │  │     │  │          G1.10       G1.1a ◄─ JASON
 │  │  │     │  └─ G2.1 ◄────┘           │
 │  │  │     │     G2.2*     ┌─────── G1.2 ────────┐
 │  │  └─ G2.7               │                     │
 │  └──── G1.12*           G1.3 (apply-queue)    G1.4
 └─ G2.4-review              │                     │
                     G1.6 → G1.7 → G1.8 → G3.3   G1.5
                             │
                    G1.9 ────┤
                             ▼
              G1.11 (reviewer) + G1.12 (runbook)
                             ▼
                   G1.13 ◄─ JASON (THE GATE)
                             ▼
              G3.9 → G4.1 → G4.2 → G4.3 → G4.4 ◄─ JASON
   * G2.2 additionally depends on G1.3+G1.4; G1.12 on G1.3–G1.5+G1.9
   G3.7 depends on G1.1a only. G2.4/G2.5/G2.6/G2.7 and G3.7 must land before G4.1.
```

### Suggested waves (maximum safe parallelism)

- **Wave 1 (now, all parallel):** ✅ Done 2026-07-06/07 — R1–R6, G1.0, G1.1, G1.10, G2.3, G2.4, G2.5, G2.6, G2.7, G3.1, G3.2, G3.4, G3.5, G3.6, G3.8.
- **Checkpoint A (JASON):** ✅ G1.1a signed off 2026-07-07, four amendments recorded.
- **Wave 2:** G1.2 ✅ done 2026-07-25 (1e665ed). G2.1 ✅ done 2026-07-26. G3.7 ✅ done 2026-07-27 (1b66559).
- **Wave 3:** ✅ Complete 2026-07-27. G1.3 (0bfab7b), G1.4 (82d64b0), G1.5 (43c9220), and the apply-queue G1.6 (dcde5c2) → G1.7 (cb96bea) → G1.8 (daa689b) → G3.3 (741cad7), plus G1.9 (adbc5a1). 234 tests + 1 xfailed (was 183 at the start of the queue).
- **Wave 4 (next):** G1.11 — the final reviewer pass is now unblocked: every Gate 1 code unit has landed (the interim pass over G1.2/G1.3 ran 2026-07-26 and does not cover G1.4–G1.9). Then G1.12. G2.2 ✅ done 2026-07-28 (206e390).
- **Checkpoint B (JASON):** G1.13 hardware validation. Loop fixes → re-validate until clean.
- **Wave 5:** G3.9, G4.1, G4.2, G4.3.
- **Checkpoint C (JASON):** G4.4 push + tag.

### Human-only ledger (everything no agent can execute)

1. **G1.1a** — revert-design sign-off (judgment that is yours).
2. **G1.13** — hardware validation: physical lab router (or CHR provisioning), lab `network.yml` with real credentials, deliberate breakage on live RouterOS.
3. **G4.4** — pushing to origin, deleting remote branches, tagging the release (outward-facing / destructive-adjacent).
4. **Standing:** production-router access of any kind during development; the lab environment's physical setup and credentials; the WiFi vault password if any unit ever thinks it needs it (none should — placeholder passphrase generation exists for previews).

### Post-1.0 (v1.1 theme: "the household can save itself")

Portal persona test suite (#44–#53), `health` (#21), `apply --diff` (#3), provision smoke tests (#4/ENH-006), WireGuard (#12), then the hardware-breadth track (#35–#37) and the `community.routeros` question (#56).

---

## Appendix — Enhancement queue reconciliation (docs/enhancements 001–025 vs code, 2026-07-06)

| ENH | Issue | Status in code | Note |
|---|---|---|---|
| 001 status→overview | #13 | **Fixed** (README) | Agents/TESTING.md still say `status` — R2/R3 + G2.3; close via G2.5 |
| 002 post-apply check | #1 | Open | G1.6 |
| 003 config error | #2 | Open (`config.py:168`) | G3.1 |
| 004 extend destroys YAML | #14 | **Fixed** (`extend.py:131`, ruamel) | Close via G2.5 |
| 005 pre-apply diff | #3 | Open | Post-1.0 |
| 006 provision smoke test | #4 | Open | Post-1.0 |
| 007 CHOOSE not validated | #15 | **Mostly fixed** (schema enums reject "CHOOSE" at load) | Verify + close via G2.5 |
| 008 rollback context | #16 | **Fixed** (43c9220, G1.5) | Closed 2026-07-27 |
| 009 audience framing | #17 | Decided (Gate 0.2, 2026-07-06) | G2.7 |
| 010 rogue remediation | #18 | Open (probe/audit say "via WinBox or SSH") | Post-1.0 |
| 011 ssid template comment | #19 | Open (`network.yml.dist:97-157`) | G3.5 |
| 012 SSH diagnosis | #5 | Open (`router.py:45`) | G3.2 |
| 013 --segment ordering | #20 | Open | G3.3 |
| 014 health command | #21 | Partially (probe covers most) | Post-1.0 |
| 015 permissions example | #22 | Open (`extend.py` never asks) | G3.6 |
| 016 card SSIDs/Pi-hole | #23 | Open (`rollback_card.py`) | G2.2 (card rewrite rides the revert-copy pass) |
| 017 probe caveat noise | #24 | Partially (now conditional on bridge setting, `probe.py:172-199`) | Annotate/close via G2.5 |
| 018 install verification | #25 | **Fixed** (README:42-50) | Python-version note lands in G2.6; close via G2.5 |
| 019 failsafe timer | #26 | **Fixed** (`apply.py:187-199`) | Close via G2.5 |
| 020 rules needs generate | #27 | Open-ish (help text only) | G3.4 |
| 021 inspect .rsc escape hatch | #28 | Partially (TESTING.md Phase 1 says it; README doesn't) | G2.6 (+G2.1) |
| 022 privacy consequences | #29 | Partially (dist comments improved vs ENH text) | Post-1.0 unless lankit-ux (after R1) flags it cheap |
| 023 rotation limit | #30 | Open (`snapshots.py:103`) | G1.8 |
| 024 wizard first-class | #31 | **Fixed** (README Option A) | Close via G2.5 |
| 025 swallowed import errors | #32 | **Mostly fixed** (`apply.py:151-184`) | Residue in G1.7 |

Five fixed, four mostly/partially fixed, none of the paperwork closed — the Part 1 diagnosis in one table.
