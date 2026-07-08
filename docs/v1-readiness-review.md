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
| G1.0 | Extract `docs/test-harness.md` from the `test-harness` branch onto main (`git show test-harness:docs/test-harness.md`), commit. Branch deletion itself waits for G4.3. | GP | — | — |
| G1.1 | **Revert-mechanism design note** (`docs/design/tag-scoped-revert.md`). Must resolve: (a) payload form — archived last-known-good generated script set vs. snapshot filtered to lankit-tagged content, and where it's stored/uploaded; (b) the first-apply case (nothing lankit-owned before → revert = remove all `lankit:*` only); (c) failsafe self-containment — the payload and the remove-pass must live *on the router* before the first script imports, and the on-event must work with no laptop attached; (d) resource-type coverage (filter, nat, mangle, address-list, interface-list members, VLAN interfaces, bridge VLAN entries, DHCP server/pool/network/lease, DNS static, wifi, queues, scheduler) and removal *ordering* so the flat/management path (defconf bridge, 192.168.88.0/24 — never lankit-owned) survives mid-revert; (e) how verification works (post-revert export compared against expected tag set) and what "fail loudly" prints in each caller. May consult `mikrotik-docs` for RouterOS `/import`, `remove [find]`, and scheduler semantics. | GP (+ mikrotik-docs as sub-consult) | — | — |
| G1.1a | **Design sign-off.** Read G1.1, approve or amend. This is the safety mechanism of the whole tool; it does not proceed on agent judgment alone. | **JASON** | G1.1 | — |
| G1.2 | Implement `lankit/core/revert.py` per the approved design: generate the revert `.rsc` (remove-pass + payload), plus the verification helper. Pure generation — no router I/O in this unit. Includes unit tests. | GP | G1.1a, G1.10 | — |
| G1.3 | **apply.py: failsafe uses the revert script.** Upload revert.rsc before any script import; scheduler on-event imports it; decline-at-prompt and import-error paths run it via the same code path; each restore verifies and fails loudly (no green ✓ over stderr). apply-queue position 1. | GP | G1.2 | part of #9 |
| G1.4 | **rollback.py / restore.py use the revert mechanism** (remove `lankit:*`, then apply target snapshot's lankit content per design), verify, fail loudly. | GP | G1.2 | #9 |
| G1.5 | **Rollback target selection + context display.** Walk backward through `pre-apply`-labeled snapshots (skip `pre-rollback`/`post-apply` markers) so double-rollback goes *further back*, never forward; show age / label meaning / size before the prompt (ENH-008 spec). | GP | G1.4 (same files) | #16 |
| G1.6 | **Post-apply probe before disarm.** After "Keep? y", run gateway pings (+ DNS check if dns_server enabled) using probe's existing helpers; on failure, warn and re-prompt with the failsafe still armed; `--skip-probe` flag. apply-queue position 2. | GP | G1.3 | #1 |
| G1.7 | **Apply error residue:** consolidated import-error summary before the prompt, `ansible/generated/apply.log`, `--strict` flag. apply-queue position 3. | GP | G1.6 | #32 |
| G1.8 | **Snapshot rotation warning + pin.** Warn at apply time near the 10-cap; `lankit snapshots --pin`; rollback shows oldest-available age. Files: snapshots.py, snapshots_cmd.py + one apply.py print (apply-queue position 4). | GP | G1.7 (for the apply.py touch only; snapshots.py work can start anytime) | #30 |
| G1.9 | **test-failsafe drills the real revert:** sentinel becomes a `lankit:`-tagged resource; the scheduler's on-event is the actual uploaded revert script; phases verify the sentinel is removed *by the revert import*, not by a targeted remove. Update the command's docstring. | GP | G1.3 | — |
| G1.10 | **Unit-test bootstrap.** `tests/unit/` per the extracted harness design: config loading/validation, generator rendering (assert on rendered `.rsc` text), snapshots index/rotation. `[project.optional-dependencies] dev` group; GitHub Actions workflow running the offline suite. Keep the existing Playwright `tests/test_*.py` untouched (they need live portals; mark/skip them in CI). | GP | G1.0 | — |
| G1.11 | **Reviewer pass** over all Gate 1 diffs (correctness, RouterOS syntax, silent-failure hunting). Findings loop back as fix-ups before G1.13. | lankit-reviewer | R5, G1.3–G1.9 | — |
| G1.12 | **Hardware-validation runbook.** A printable checklist mapping each break scenario to the code path it exercises: mid-apply SSH kill (failsafe fires → revert), bad script (fail-stop → revert), decline at prompt, double rollback (must go further back), first-apply revert, restore-over-live. Includes expected observations and the exact commands. | lankit-auditor | R2, G1.3–G1.5, G1.9 | — |
| G1.13 | **HARDWARE VALIDATION SESSION.** Lab router per `docs/test-harness.md` (or CHR), lab `LANKIT_CONFIG`, run the G1.12 runbook, deliberately break a working config every way listed, confirm every path recovers. Only after a full lab pass does anything touch the production router. **This is the v1.0 gate.** | **JASON** (agents may assist interpretation) | G1.12 | — |

### Gate 2 — Truth reconciliation

| Unit | What | Owner | Depends | Closes |
|---|---|---|---|---|
| G2.1 | **DESIGN.md mechanical truth pass** (everything *except* the safety sections): `kit:` → `lankit:` convention; replace the fictional CLI section with the real command set; file-structure/architecture sections (Flask portals under ansible, actual core modules, delete the FastAPI Interface Contract or mark historical); real `discover` flags; `extend`/`restore` semantics; move unbuilt subsystems (registry, add/move/identify/rule, aws-setup, pki --share, generated testinfra suite) to an explicit "Future / not built" section; fix the audience opening line per Gate 0.2. | lankit-doc | R6 (audience line also G2.7 coordination) | #28 partial |
| G2.2 | **Safety-surface copy rewrite** — everything that describes the revert mechanism, after it exists: DESIGN.md failsafe/rollback sections; `network.yml.dist` Safety comment (the provision/safe-mode/commit errors); TESTING.md Phase 4; `rollback_card.py` content — Option 2/3 rewritten for the new mechanism **plus** the ENH-016 additions (SSID list referencing the password card, Pi-hole admin URL, regenerate-both-cards reminder). | lankit-doc (card code edit may sub-delegate to GP) | R6, G1.3, G1.4 | #23 |
| G2.3 | **TESTING.md non-safety fixes:** `status`→`overview`; delete the stale extend/yaml.dump warning (fixed) and the `/tmp` claim; remove the "before creating the repo" checklist. | GP | — | — |
| G2.4 | **Persona re-key + archive freeze:** mechanical rename across `docs/enhancements/*` (Alex→Priya, Morgan→Dale, Sam→Tariq, Jordan→Renata, Riley→Yemi); add an archive banner (`README` line in the directory: "frozen 2026-07 — live queue is GitHub issues"). Post-repair `lankit-ux` sanity-reviews the mapping (it's judgment-free, but cheap to check). | GP, review by lankit-ux | review leg: R1 | — |
| G2.5 | **Issue hygiene:** close #13, #14, #25, #26, #31 with commit-citing comments; verify-and-close or annotate #15 (ENH-007, mostly fixed) and #24 (ENH-017, partial); comment on #12 recording the "marked not-implemented for v1.0" default; #33 closes via G3.8. | GP (gh auth already present locally) | — | #13 #14 #25 #26 #31 (+#15/#24 judgment) |
| G2.6 | **README pass** (single unit — the file is small and these collide): portal paragraph rewritten per Gate 0.3 (beta/optional/off-by-default, what actually exists); quick-start step to inspect `ansible/generated/*.rsc` between generate and apply (ENH-021); `python --version` check note (ENH-018 residue). | lankit-voice | — | #28, finishes #25 |
| G2.7 | **Mission/audience closure:** one-paragraph addition to `docs/mission.md` recording the Gate 0.2 resolution (installer technical; household served via outputs) + revision-history line; confirm DESIGN.md opening line (landed via G2.1) satisfies it; close #17 with a comment stating the resolution. | lankit-mission | R3 (G2.1 for the DESIGN.md line) | #17 |

### Gate 3 — Onboarding polish (all independent unless noted)

| Unit | What | Owner | Depends | Closes |
|---|---|---|---|---|
| G3.1 | ENH-003: actionable missing-config error in `config.py:168-171` (name `cp network.yml.dist network.yml`, `lankit discover --new`, `LANKIT_CONFIG`). | GP | — | #2 |
| G3.2 | ENH-012: classify SSH failures (refused/timeout, auth, no-route) in a shared helper used by every RouterError catch; per-class checklists per the spec. | GP | — | #5 |
| G3.3 | ENH-013: `--segment` mode checks for `lankit:fw:all:default-deny` on the router; warn + confirm if absent; next-steps line after segment apply. apply-queue position 5 (last). | GP | G1.7 (apply-queue) | #20 |
| G3.4 | ENH-020: `rules` and `diagram` print a clear "run lankit generate first" pre-flight when `ansible/generated/` is empty/absent. | GP | — | #27 |
| G3.5 | ENH-011: comment in `network.yml.dist` explaining `{{ household_name }}` expansion with a concrete example, above first templated SSID. | GP | — | #19 |
| G3.6 | ENH-015: permissions-section example comment for new segments + `extend` wizard asks "which segments can reach this one?" (validated names, written to `permissions:`). | GP | — | #22 |
| G3.7 | S6 fasttrack: script the defconf-fasttrack disable in `03-firewall.rsc.j2` under a `lankit:` tag (so the revert design covers re-enabling it), or — if G1.1 concluded that's unsafe — have `audit`/`probe` flag the undone manual step. Must follow the approved revert design either way. | GP | G1.1a | — |
| G3.8 | Dependency prune: remove `weasyprint` and `pytest-testinfra` from `pyproject.toml`; mark `vpn:` not-implemented in dist comment (+ schema description string). | GP | — | #33, updates #12 |
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

- **Wave 1 (now, all parallel):** R1–R6, G1.0, G1.1, G2.3, G2.4, G2.5, G2.6, G3.1, G3.2, G3.4, G3.5, G3.6, G3.8. Then G1.10 as soon as G1.0 lands, G2.7 after R3.
- **Checkpoint A (JASON):** G1.1a design sign-off.
- **Wave 2:** G1.2; G2.1 (after R6); G3.7.
- **Wave 3:** G1.3 and G1.4 in parallel (different files); then G1.5, G1.6→G1.7→G1.8→G3.3 (apply-queue), G1.9.
- **Wave 4:** G1.11, G1.12, G2.2.
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
| 008 rollback context | #16 | Open (`rollback.py:41`) | G1.5 |
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
