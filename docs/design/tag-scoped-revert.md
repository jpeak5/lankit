# Design: Tag-Scoped Revert (G1.1)

- **Status:** **signed off 2026-07-07 (checkpoint G1.1a), with four amendments below.** G1.2 may proceed.
- **Date:** 2026-07-06 (amended 2026-07-07)
- **Unit:** G1.1 of the v1.0 execution plan (`docs/v1-readiness-review.md`, Part 4)
- **Consumers:** G1.2 (`lankit/core/revert.py`), G1.3 (`apply.py` wiring), G1.4 (`rollback.py`/`restore.py`), G1.5 (rollback target selection), G1.9 (`test-failsafe` drill), G3.7 (fasttrack), G2.2 (safety copy)
- **Decided context (not relitigated here):** automatic recovery reverts by `lankit:` tag ownership — remove lankit-owned resources, rebuild known-good lankit state. Additive import of a raw `/export` snapshot remains valid only after a factory reset (rollback-card path).

---

## 1. Problem summary

Every automatic recovery path today — the failsafe scheduler, the apply decline/import-error paths, `lankit rollback`, `lankit restore` — "restores" by running `/import` of a raw `/export verbose` snapshot over the live config (`apply.py:135-138, 256-268`; `rollback.py:58-62`; `restore.py:97-101`). RouterOS `/import` is additive and halts at the first error; against a config that a bad apply just added rules to, the snapshot's `add` commands hit duplicates almost immediately, the import aborts, and the bad rules stay. The scenario the failsafe exists for is the scenario where snapshot import is least likely to work (readiness review, Part 2, finding S1).

Compounding it, all three CLI restore paths print import stderr as a yellow warning and then print a green success mark (S1), and `test-failsafe` certifies the timer with a targeted rule-remove, never the actual revert payload — false assurance on exactly the property `docs/mission.md:66` calls non-negotiable. This design specifies the replacement mechanism: a single, self-contained, idempotent **revert bundle** (`lankit-revert.rsc`) that removes everything lankit owns and rebuilds the last known-good lankit state, plus the verification and loud-failure contract every caller must obey.

## 2. Mechanism overview

One generated file, `lankit-revert.rsc`, with two parts:

1. **Universal remove-pass** — generated from a fixed, canonical registry of every RouterOS table lankit writes to (§5). Removes every row whose comment matches `^lankit:`, in a dependency-safe order, each block wrapped in `:do {} on-error={:log ...}` so one surprise cannot abort the whole pass. Also undoes lankit's small set of untagged `set` mutations (§5.3). Running the remove-pass alone converges the router to "defconf plus whatever the operator did manually" — the flat 192.168.88.0/24 management network.
2. **Rebuild payload** — the archived last-known-good generated script set (§3), concatenated in normal apply order (`01-…` through `07-…`). Because every generated script is remove-then-add idempotent, importing script-set *S* converges lankit-owned state to *S* regardless of prior lankit state. The archived scripts' own scoped remove-passes run as harmless no-ops after the universal pass.

The bundle is importable on its own, re-runnable if it fails halfway (§5.5), and is uploaded to the router **before** the first apply import begins, so the failsafe needs nothing from the laptop (§4… see §6).

Key property this design leans on throughout: **importing the bundle is convergence, not diff-undo.** The contract is "lankit-owned state ends equal to the archived known-good state"; it is not a byte-faithful rewind of non-lankit config (which lankit never touches anyway, minus the enumerated `set` mutations).

## 3. Decision (a) — Payload form

**Decision: Option 1 — archive the last-known-good generated script set at each successful apply; the revert bundle for the next apply is [universal remove-pass] + [that archive].**

### Options compared

**Option 1 — archived known-good script set.** At each successful apply (user answered "keep"), lankit archives the rendered `.rsc` set that now describes live lankit state, under `~/.lankit/known-good/<router-ip>/<timestamp>/` with a `manifest.json` (created-at, `network.yml` content hash, `segments_applied`, `globals_applied`, paired snapshot id). The next apply's failsafe payload is built from this archive.

**Option 2 — pre-apply `/export` snapshot filtered to lankit-tagged content, with a generated remove-pass prepended.** Parse the export, keep only sections/items whose comment matches `lankit:`, emit as the rebuild payload.

### Why Option 1

1. **Not all lankit state carries a tag.** lankit's applied state includes plain `set` mutations on rows it does not own: `vlan-filtering=yes` on the defconf bridge, `pvid` on the DNS-server port (`01-vlans.rsc.j2:63`), master-WiFi hide/rename (`04-wifi.rsc.j2:26-31`), and — once G3.7 lands — the defconf-fasttrack disable. A tag-filtered export cannot see any of these; re-running the generated scripts reproduces all of them. Option 2 rebuilds a network with VLAN filtering possibly off, the Pi's port on the wrong PVID, and masters in an unknown state. This alone is disqualifying.
2. **Exports omit sensitive values.** `/export verbose` (what `export_config()` runs) hides passphrases unless `show-sensitive` is given. An Option-2 rebuild recreates every `interface/wifi/security` profile with an empty passphrase — every SSID broken after a "successful" revert. Option 1 renders with the passphrases apply already loaded into memory.
3. **Guaranteed importability.** The generated scripts are the artifact lankit already proves importable on every apply, in a known order, with dependency ordering designed in. A tag-filtered subset of an export is a novel program assembled by a parser: RouterOS export line-continuations and per-version property sets make that parser fragile, and a parser bug becomes a recovery-path bug — the worst place to have one.
4. **`network.yml` edits between applies are handled correctly.** The archive is rendered *text captured at success time*, never re-rendered at revert time. If the user renames or deletes a segment in `network.yml` after the last apply, the revert still rebuilds exactly the state that was live, not the edited intent. (Option 2 also has this property; Option 1 does not lose it.)
5. **First apply** degrades naturally to "no archive → remove-pass only" (§4), matching Option 2's one genuine advantage.

### Archive update rules

- **Full apply, kept:** archive := the just-applied script set, verbatim. `segments_applied` = all segments in the applied config; `globals_applied` = true.
- **Segment apply (`--segment X`), kept:** `segments_applied` := previous ∪ {X}; `globals_applied` unchanged; archive := re-render from the *current* `network.yml` (which is the yml that just produced the live slice) for exactly that segment set with that globals flag. This requires G1.2 to extend the generator's `segment_filter` from "one name" to "a set of names + explicit `include_globals`" — the templates already iterate an arbitrary `segments` dict, only `_build_context()` restricts it (`generator.py:121-132`).
- **Never** update the archive on decline, import error, failsafe fire, rollback, or restore. Rollback/restore change which archive is *selected* (§8), not its content.

### Accepted costs (stated for sign-off)

- **Plaintext WiFi passphrases on disk** in `~/.lankit/known-good/` (dir `0700`, files `0600`). **Sign-off note (2026-07-07):** verified against the running code — `ansible/generated/04-wifi.rsc` already contains plaintext passphrases today, on every `lankit generate`/`lankit apply`, with zero permission hardening (`generator.py` sets no file mode). The archive's stated `0700`/`0600` is strictly tighter than that existing, unprotected exposure — this design does not introduce a new class of risk, and accepting it does not require accepting anything worse than the status quo. Contingent on the retention-sweep mechanism (§8, §12) actually shipping in G1.2, so archives don't accumulate unboundedly. `ansible/generated/`'s own lack of permission hardening is a separate, smaller, non-blocking follow-up (not tracked by this design).
- **Convergence semantics under drift.** If segment B's live state drifted (manually, or via a yml edit + segment-apply of A only) the revert converges B to the archived declared state, not to its drifted live state. This is the same behavior as re-running `lankit apply` and is consistent with the no-manual-drift non-negotiable — but it is not a literal undo, and the docs (G2.2) must say so.

## 4. Decision (b) — First apply

Before the first successful apply, nothing lankit-owned exists, so revert = **universal remove-pass only** (plus the `set`-mutation restorations in §5.3, which are no-ops on an untouched router because every block is find-guarded).

**How the generator knows:** the payload builder takes an explicit `archive: KnownGoodArchive | None` argument. The caller (apply, G1.3) resolves the archive for `cfg.router.ip`; **absence of the archive directory/manifest for that router is the first-apply signal.** No heuristics against the live router, no guessing from snapshots. `revert.py` renders:

- `archive is None` → remove-pass only, terminated with `:log info "lankit-revert: remove-only revert complete"`.
- `archive` present → remove-pass + concatenated archive scripts.

The expected-tag set used by verification (§7) is derived from the same argument: empty set for `None`, manifest-derived set otherwise. One input drives both the payload and its acceptance test, so they cannot disagree.

Upgrade wrinkle (existing lankit routers have live lankit state but no archive yet): see migration, §9.

## 5. Decision (d) — Resource coverage and removal ordering

### 5.1 Canonical table registry

`revert.py` (G1.2) owns a single canonical registry: every RouterOS table lankit writes to, the comment shapes it uses there, and the print command used to read tags back. Cross-checked against all seven templates, `apply.py`, and `probe.py`:

| # | Table | lankit comment shapes | Written by |
|---|---|---|---|
| 1 | `/system scheduler` | name `lankit-failsafe`, comment `lankit:failsafe` | apply.py |
| 2 | `/ip firewall filter` | `lankit:fw:<seg>:isolation`, `lankit:fw:<src>><dst>:permit`, `lankit:fw:<seg>>dns:permit-udp/tcp`, `lankit:fw:<seg>:egress`, `lankit:fw:all:default-deny`, `lankit:fw:all:fasttrack-internet` | 03 |
| 3 | `/ip firewall nat` | `lankit:nat:all:masquerade`, `lankit:fw:<seg>:dns-redirect-udp/tcp` | 03, 06 |
| 4 | `/ip firewall mangle` | `lankit:fw:all:mark-internet`, `lankit:bw:<seg>:mark-upload/download` | 03, 07 |
| 5 | `/ip firewall address-list` | `lankit:fw:<seg>:address-list`, `lankit:fw:all:local-rfc1918`, `lankit:fw:all:local-flat` | 03 |
| 6 | `/queue tree` | `lankit:bw:<seg>:up/down` | 07 |
| 7 | `/ip dhcp-server lease` | `lankit:dhcp:dns-server:lease`, `lankit:dhcp:app-server:lease` | 02 |
| 8 | `/ip dhcp-server network` | `lankit:dhcp:<seg>:network` | 02 |
| 9 | `/ip dhcp-server` | `lankit:dhcp:<seg>:server` | 02 |
| 10 | `/ip pool` | `lankit:dhcp:<seg>:pool` | 02 |
| 11 | `/ip address` | `lankit:dhcp:<seg>:address` | 02 |
| 12 | `/ip dns static` | `lankit:dns:<seg>:gateway`, `lankit:dns:{dns-server,pihole,app-server,portal-me,portal-network,portal-register}:hostname` | 05 |
| 13 | `/interface bridge port` | `lankit:wifi:<seg>:bridge-5g/2g` | 04 |
| 14 | `/interface wifi` | `lankit:wifi:<seg>:ap-5g/2g` | 04 |
| 15 | `/interface wifi security` | `lankit:wifi:<seg>:security` | 04 |
| 16 | `/interface list member` | `lankit:fw:<seg>:lan-member` | 03 |
| 17 | `/interface bridge vlan` | `lankit:vlan:<seg>:bridge`, `lankit:vlan:legacy:flat` | 01 (04 `set`s `untagged=` on these owned rows — covered by re-add) |
| 18 | `/interface vlan` | `lankit:vlan:<seg>:interface` | 01 |

**Cross-check finding (fixed in G1.2 — see sign-off note):** `probe.py` is *not* the complete inventory the plan assumed. `_read_live_tags()` misses `/ip dns static`, `/queue tree`, and `/system scheduler`; `_build_expected_tags()` misses every `lankit:dns:*`, every `lankit:bw:*`, `lankit:dhcp:app-server:lease`, and `lankit:fw:<seg>:dns-redirect-*`. The registry above supersedes it; the revert verification helper must use the registry.

**Sign-off note (2026-07-07):** probe's migration onto `TAG_TABLES` is **in scope for G1.2**, not a deferred follow-up. Leaving `probe.py` on its own hand-maintained, already-wrong lists while a correct registry exists elsewhere is exactly the truth-decay pattern the v1-readiness review diagnosed as this project's stall cause (Part 1) — deferring it here would reproduce that pattern inside the fix meant to close it. `probe.py`'s `_build_expected_tags()`/`_read_live_tags()` should be replaced with calls into `revert.py`'s registry.

### 5.2 Removal order

The remove-pass executes in this order. Every step is a find-guarded `remove`/`set` that is a no-op when nothing matches.

1. `/system scheduler remove [find where name="lankit-failsafe"]` — no-op when fired *by* the failsafe (the on-event already self-deleted, §6); necessary when the bundle is run by rollback/restore or by hand so a pending failsafe can't fire mid-revert. Matches on the exact name only — never a broad comment match against user schedulers.
2. `/interface bridge set bridge vlan-filtering=no` — **the management-path anchor.** From this point the bridge forwards flat; no subsequent removal can partition an untagged port from the CPU.
3. `/ip firewall filter` — remove `comment~"^lankit:"`. Fail-open: the lankit default-deny disappears before anything else, so mid-revert the network errs toward availability, not isolation (accepted for the home threat model; documented in G2.2).
4. `/ip firewall nat`, `/ip firewall mangle`, `/ip firewall address-list` — remove `^lankit:`.
5. `/queue tree` — remove `^lankit:`.
6. DHCP chain, references-first (mirrors 02's own remove order): `lease` → `dhcp-server network` → `dhcp-server` → `pool` → `/ip address`.
7. `/ip dns static` — remove `^lankit:`.
8. WiFi, references-first (mirrors 04): `bridge port` (`^lankit:`) → `wifi` virtual APs → `wifi security` profiles.
9. `/interface list member` — remove `^lankit:` (before the VLAN interfaces they reference disappear).
10. `/interface bridge vlan` — remove `^lankit:`.
11. `/interface vlan` — remove `^lankit:` (last: addresses, list members, and bridge-vlan rows referencing them are already gone).
12. Untagged-mutation restore (§5.3).

### 5.3 Untagged `set` mutations lankit must restore

These carry no `lankit:` comment; the remove-pass generator emits explicit restorations, each find-guarded:

| Mutation (where lankit sets it) | Restore in remove-pass | Source of the restore value |
|---|---|---|
| `bridge vlan-filtering=yes` (01) | `set bridge vlan-filtering=no` (step 2) | constant (defconf value); the rebuild payload re-enables it when `globals_applied` |
| DNS-server port PVID (01: `set [find where interface=<lan_interface>] pvid=<dns vlan>`) | `set [find where interface=<lan_interface>] pvid=1` | `lan_interface` from the archive's manifest (current `network.yml` for first-apply) |
| ether3–5 PVID (01 sets them *to* 1) | `set ... pvid=1` — belt-and-suspenders no-op | constant |
| Master WiFi hide/rename (04: `wifi1`/`wifi2` → `_master-*-noconnect`, hidden) | restore captured `configuration.ssid` / `hide-ssid` values | captured live by the **caller** at bundle-generation time (apply reads two fields over the existing connection and passes them in); if capture fails, omit the block and warn — masters stay hidden, wired path unaffected |
| defconf fasttrack disable (future, G3.7) | `enable [find where comment="defconf: fasttrack" && disabled=yes]` | constant; only emitted once G3.7 lands. G3.7 must not tag the defconf rule with `lankit:` (the remove-pass would try to delete a defconf rule); it disables it and this design re-enables it. |

`revert.py` stays pure generation: all live-read inputs (master-WiFi capture) are parameters supplied by the caller, per the G1.2 no-router-I/O rule.

### 5.4 Why the management path survives at every intermediate point

The management path is: defconf `bridge`, physical ether ports as defconf bridge ports, `192.168.88.1/24` on the bridge (defconf `/ip address`), the defconf DHCP server, and the defconf input-chain accepts. **None of these carry a `lankit:` comment; no remove step can match them.** The two ways lankit *could* interfere are both closed:

- **L2:** the only lankit hold on flat forwarding is `vlan-filtering=yes` plus the `lankit:vlan:legacy:flat` bridge-VLAN row. Step 2 disables filtering before any bridge-VLAN row is touched, so removing that row (step 10) is irrelevant to forwarding. Even without step 2 there is a documented second net: RouterOS adds dynamic bridge-VLAN entries per port PVID ("a dynamic entry is added in the bridge VLAN table for every PVID used, the port is automatically added as an untagged port" — help.mikrotik.com, Bridge VLAN Table), and ether3–5 and the bridge itself sit on PVID 1. Step 2 makes survival unconditional rather than dependent on that behavior; G1.13 verifies the dynamic-entry behavior on the hAP ax³ anyway (Q1).
- **L3/firewall:** lankit adds only *forward*-chain and NAT rules plus permits; the defconf *input*-chain accepts for LAN are untouched, and removing lankit filter rules only ever opens traffic, never closes it. Removing `lankit:fw:<seg>:lan-member` list entries affects VLAN interfaces only — the physical ports' LAN membership is defconf's.

Mid-revert WiFi honesty: between step 8 and the rebuild's `04-wifi` section, lankit SSIDs are gone and masters are still hidden. **The guaranteed survival path during a revert is wired, on 192.168.88.0/24** — exactly what the rollback card already tells the household to use. The master-restore block (§5.3) and the rebuild close the gap at the end.

### 5.5 If the revert import fails halfway

- Every remove-pass block is wrapped in `:do { ... } on-error={:log warning "lankit-revert: <table> remove failed"}` — one unexpected removal error logs and continues rather than aborting the pass. This does not hide failure: success is decided by verification (§7), never by the script finishing.
- The rebuild payload (the archived scripts) is left unwrapped, exactly as apply imports it: an `add` failure aborts the import at that line, RouterOS logs it, and verification reports the shortfall.
- **The bundle is re-runnable:** the remove-pass is find-guarded (no-ops on already-removed rows) and the payload is remove-then-add idempotent, so a second `/import file=lankit-revert.rsc` — by the user over SSH, or by a rollback retry — converges from any partial state. `/import` does not delete the file, and files on the hAP ax³ flash root persist across reboots, so the bundle remains available for manual re-run.
- Terminal breadcrumb: the last line of the bundle is `:log info "lankit-revert: complete"` — it exists in `/log` only if the import reached the end (used by the failsafe path, §7).

## 6. Decision (c) — Failsafe self-containment

Everything the scheduler needs is on the router before the first apply import begins; the on-event works with no laptop attached. Order of operations in apply (G1.3):

1. Connect; save pre-apply snapshot (unchanged — still valuable for humans and the factory path).
2. Capture master-WiFi facts; **build `lankit-revert.rsc` locally** from the current known-good archive (or `None` → remove-only).
3. **Upload `lankit-revert.rsc` to the flash root and verify it landed** (`/file print where name="lankit-revert.rsc"` — check presence and non-trivially-truncated size) before proceeding. No scheduler, no imports, until the bundle is confirmed on-router.
4. Install the failsafe scheduler. The on-event stays a single self-contained command sequence, preserving the proven self-deleting one-shot shape:

   ```
   /system scheduler remove [find name="lankit-failsafe"];
   :log warning "lankit-failsafe: fired - importing lankit-revert.rsc";
   :do { /import file=lankit-revert.rsc;
         :log warning "lankit-failsafe: revert import finished" }
   on-error={ :log error "lankit-failsafe: revert import FAILED - run lankit probe" }
   ```

   Self-delete first (unchanged — cannot fire twice); log markers give the no-user-present failure signal (§7); the `:do/on-error` keeps the on-event itself from dying silently. All revert logic lives in the uploaded file, keeping the on-event short and static.
5. Only now: upload and import the apply scripts, re-arm before the confirm prompt, etc. (existing hardened flow unchanged).

The decline-at-prompt and import-error paths run the **same** `lankit-revert.rsc` via `/import` over the live SSH session (replacing `_restore_snapshot`'s snapshot upload), so the interactive and unattended paths exercise one code path and one artifact. Single-file design deliberately avoids multi-import sequencing inside the on-event.

Filename: `lankit-revert.rsc` (new name). `lankit-restore.rsc` remains the raw-snapshot name used by the factory-reset path, so the two mechanisms are never confused on the router's file listing.

Pre-existing residual limitation, unchanged by this design (out of scope, noted for honesty): if the router *reboots* during the failsafe window, the scheduler's start time passes while it is down and the one-shot does not fire on boot; recovery is then manual (`lankit rollback` or on-router `/import file=lankit-revert.rsc` — which this design at least makes possible and re-runnable).

## 7. Decision (e) — Verification and loud failure

### 7.1 The verification helper (G1.2)

`verify_revert(live_tags: set[str], expected_tags: set[str]) -> RevertVerdict` — pure comparison; a thin caller-side reader collects `live_tags` by running the registry's print commands (§5.1 — superset of probe's list, adding dns static, queue tree, scheduler). `expected_tags` derives from the same archive object that built the payload (empty set for remove-only). Verdict:

- `missing` — expected tags absent (rebuild fell short),
- `unexpected` — live `^lankit:` (or legacy `^kit:`) tags not in the expected set (remove-pass fell short),
- `ok` ⇔ both empty.

**Global invariant, all callers:** a green success mark may be printed only when (import stderr was clean per the existing error classifier) **and** (`verify_revert` returned ok). Any other combination prints failure, shows the evidence, and exits non-zero. No exceptions.

### 7.2 Per-caller behavior

| Caller | Verifies by | On success | On failure |
|---|---|---|---|
| **apply, decline path** ("Keep? n") | import bundle over live session → read tags → `verify_revert` | `✓ Reverted — router matches last known-good state` | bold red `✗ REVERT INCOMPLETE — router may be in a mixed state.` Print stderr excerpt + `missing`/`unexpected` tag lists. Print next steps verbatim: re-run on-router (`ssh <user>@<ip> '/import file=lankit-revert.rsc'`), then `lankit probe`; escalation: `lankit rollback`, then the rollback card. Exit 1. |
| **apply, import-error path** | same code path as decline | `Restored. No changes were kept.` (only after verification) | same as decline, prefixed with which script's import failed. Exit 1. |
| **failsafe** (no user present) | cannot verify from the router alone. Signals: `/log` markers from §6 (`fired` / `finished` / `FAILED`) + the bundle's terminal `lankit-revert: complete` marker | n/a (silent by nature) | Two-layer surfacing: (1) docs + rollback card instruct: *after any failsafe fire, run `lankit probe`* — which, on the shared registry, reports exactly the missing/unexpected tags; (2) G1.3/G1.4 add a cheap check on every subsequent lankit connect: scan `/log print where message~"lankit-failsafe"` for a recent `FAILED`/missing-`complete` sequence and print a prominent warning before doing anything else. **Sign-off amendment (2026-07-07): the `/log` scan is not sufficient on its own** — RouterOS's in-memory log buffer is small and rotates under normal traffic; the exact scenario this layer exists for (nobody reconnects for weeks — Yemi, Clem) is the scenario most likely to have already lost the log line. G1.3 must add a durable marker independent of log rotation: on-event writes `/system note` (or a small lankit-owned, non-tag-scanned sentinel — e.g. a disabled scheduler entry named `lankit-failsafe-fired` left behind deliberately, never auto-removed) recording that the failsafe fired, checked on every subsequent connect in addition to (not instead of) the `/log` scan. This durable marker must be cleared explicitly by the next successful `apply`'s "keep" path, not by the revert itself. |
| **rollback** | after importing the bundle for the selected target: read tags → `verify_revert` against that target's expected set | `✓ Rolled back to <target> — verified (N lankit resources)` | bold red `✗ Rollback did NOT complete.` — never a green mark over stderr (closes the S1 swallow at `rollback.py:60-64`). Evidence lists + next steps (retry, `lankit restore` with another snapshot, card). Exit 1. |
| **restore** | same as rollback with the chosen snapshot's expected set | `✓ Restored <name> — verified` | mirror of rollback wording (closes `restore.py:99-103`). Exit 1. |

`test-failsafe` (G1.9) applies the same invariant: PASS requires the sentinel gone **and** expected tags present **and** the log markers found — never timer-fired alone.

## 8. Interaction with `lankit rollback` target selection (G1.5) — what "restore snapshot X" means

Under the new mechanism, **"restore snapshot X" means: converge lankit-owned state to the lankit state that was live when X was captured** — realized by importing [remove-pass] + [the known-good archive entry paired with X]. The raw export X itself is no longer executed against a live config; it remains stored for human inspection/diff and for the factory-reset additive path.

Pairing: every `snapshots.save()` records the id of the known-good archive that was current at capture time (sidecar field in the snapshot index; G1.2/G1.4 detail). Post-apply snapshots pair with the archive written by that apply; pre-apply / pre-rollback / pre-restore snapshots pair with the archive that was current *before* the operation. Archive entries are retained as long as any snapshot in the index pairs to them (they are small — tens of KB of text).

**Sign-off amendment (2026-07-07) — retention mechanism, not just intent:** the sentence above states the desired invariant but the original draft specified no enforcement. G1.2 must implement it concretely: whenever `snapshots._update_index()`'s rotation drops the oldest entry off the 10-cap (existing behavior, `snapshots.py:103`), check whether any *remaining* snapshot (for that router) still pairs to the evicted entry's archive id; if none do, delete that archive directory. This closes the same silent-accumulation failure mode already found in the snapshot layer itself (issue #57) before it reopens in the new archive store — and it matters more here, since each unreachable archive also holds plaintext WiFi passphrases (§3).

G1.5 is orthogonal by construction: it changes *which* X rollback selects (walk backward through `pre-apply`-labeled snapshots, skip `pre-rollback` markers, show context); this design only defines what acting on X does. Note for G1.5: because "restore X" is convergence to X-time lankit state, double-rollback under the new mechanism is idempotent when it lands on the same target and strictly older when G1.5 walks further back — the S2 oscillation is closed by the two units together.

Snapshots without a pairing (see §9) get an honest refusal path, not a silent downgrade.

## 9. Migration — existing raw snapshots, existing routers

- **Existing snapshots in `~/.lankit/snapshots/` remain valid for:** (1) the factory-reset additive-import path on the rollback card (the one case where import-over-config works, because the config is near-empty), and (2) human inspection/diffing. **They are not usable as tag-scoped rebuild payloads** — no reliable tag filtering (§3, reasons 1–3) and no sensitive values. When rollback/restore selects an unpaired snapshot, the command says exactly that and offers: (a) remove-pass-only revert (converge to defconf + manual state — flat network stays up), or (b) the factory-reset + additive-import instructions. It never pretends a tag-scoped restore happened.
- **Existing lankit-provisioned routers have live lankit state but no archive.** The first post-upgrade `apply` must not silently arm a remove-only failsafe (a fire would drop every segment back to flat .88 — survivable but a nasty surprise). G1.3 therefore adds a **bootstrap step**: if no archive exists but live `^lankit:` tags are found, run the registry tag-audit; if live state matches the full render of the current `network.yml`, offer "Record current state as known-good? [y/N]" and write the archive before proceeding. If it does not match, refuse to proceed until the user either runs a full apply eyes-open (which creates the archive on keep) or explicitly acknowledges a remove-only failsafe for this one apply.

## 10. RouterOS version assumptions

- **RouterOS 7.x; tested device hAP ax³.** Specifically assumed: `/interface wifi` menu naming (7.13+ wifi package — matches the existing templates, not new); `find where comment~"^..."` regex matching (already relied on by every template); `:foreach`/`:do on-error` scripting; scheduler `on-event` inline scripts with self-removal (already proven by `test-failsafe`); dynamic bridge-VLAN entries from PVIDs (documented; hardware-verified in G1.13); `/import` additive, halt-on-first-error, file retained after import; flash-root files persist across reboot on this device.
- Not assumed: `/export` output format stability (deliberately — the design avoids parsing exports at all), RouterOS safe-mode, `/system reset-configuration` (explicitly deferred).

## 11. Rejected alternatives

- **Tag-filtered pre-apply export as rebuild payload (Option 2):** cannot reproduce untagged `set` mutations, loses passphrases, requires a fragile export parser in the recovery path (§3).
- **Reset-based restore (`/system reset-configuration run-after-reset=`):** strongest guarantee but maximally disruptive and terrifying on an unattended timer; already deferred by decision (review Part 2, option 2).
- **Keep additive snapshot import + verification only ("minimum honest v1.0"):** verifies loudly that recovery *didn't* work; fails the mission's rollback-first bar (review option 3).
- **Re-render the payload at revert time from `network.yml`:** reverts to *edited intent* instead of last-known-good when the yml changed between applies; archiving rendered text at success time is strictly safer.
- **Multi-file revert (remove.rsc + payload scripts, sequenced in the on-event):** more on-event logic to get wrong unattended; a single concatenated file keeps the on-event static and the artifact atomic.
- **Broad "reset all bridge port PVIDs to 1" in the remove-pass:** could clobber operator-owned PVIDs; restore only the ports lankit actually set, by name (§5.3).
- **Tagging the defconf fasttrack rule `lankit:` when G3.7 disables it:** the remove-pass would then *delete* a defconf rule; disable/enable it untagged instead (§5.3).

## 12. Implementation sketch (unit mapping)

- **G1.2 — `lankit/core/revert.py` (pure generation + verification, no router I/O):**
  - `TAG_TABLES` canonical registry (§5.1): table path, comment-match pattern, read-back print command, removal-order position.
  - `build_remove_pass(lan_interface, master_wifi_facts=None, fasttrack_reenable=False) -> str` — §5.2 order, `:do/on-error` wrapping, log breadcrumbs.
  - Known-good archive store (`~/.lankit/known-good/<router-ip>/…` + `manifest.json`): `save_known_good(...)`, `load_known_good(router_ip, archive_id=None)`, pairing helpers for the snapshot index, **and `sweep_orphaned_archives(router_ip)` — called from `snapshots._update_index()`'s rotation path; deletes any archive with zero remaining paired snapshots (sign-off amendment, §8).**
  - `build_revert_bundle(archive | None, **remove_pass_inputs) -> str` — remove-pass + concatenated archive scripts + terminal log marker; `expected_tags(archive | None) -> set[str]`; `verify_revert(live, expected) -> RevertVerdict`.
  - Generator extension: `segment_filter` accepts a set of segment names + explicit `include_globals` (for the segment-apply archive refresh, §3).
  - **`probe.py` migrated onto `TAG_TABLES`** (sign-off amendment, §5.1) — `_build_expected_tags()`/`_read_live_tags()` replaced with calls into the shared registry, in this unit, not deferred.
  - Unit tests: rendered-text assertions for remove-pass order/wrapping, first-apply (`None`) case, expected-tag derivation vs. the templates' actual comment shapes, manifest round-trip, bundle re-runnability shape (removes precede adds).
- **G1.3 — apply.py:** the §6 sequence (capture facts → build bundle → upload → **verify upload** → scheduler with new on-event → imports); decline and import-error paths import `lankit-revert.rsc` + verify + loud-fail per §7.2 (replacing `_restore_snapshot`); on keep: write archive + manifest, pair the post-apply snapshot, **clear the durable failsafe-fired marker if present (§7.2 amendment)**; migration bootstrap prompt (§9); failsafe check on connect (§7.2) — **both the `/log` scan and the durable marker, not the log scan alone**.
- **G1.4 — rollback.py / restore.py:** resolve target snapshot → paired archive (§8); build/upload/import bundle; verify + loud-fail per §7.2; unpaired-snapshot honest path (§9); share the connect-time failsafe-log check.
- **G1.5 — target selection:** unchanged by this design except the definition of "restore X" (§8); pre-rollback snapshots still pair to their pre-operation archive so walking backward lands on real states.
- **G1.9 — test-failsafe drill (amended 2026-07-07, resolves Q7 as a default/`--full` split, not a single behavior):**
  - **Default (unchanged risk profile from today):** sentinel becomes a `lankit:`-tagged resource (disabled filter rule `comment="lankit:test:sentinel"`); the scheduler on-event is the real §6 on-event *shape* (self-delete → `:do{/import ...}on-error{...}` → log markers), but the imported file is a **minimal one-line removal script** (`/ip firewall filter remove [find comment="lankit:test:sentinel"]`), not the full production bundle. This proves the mechanical primitive that matters — scheduler self-delete, on-event-triggered import, log-marker emission, verification — without touching any segment, VLAN, WiFi, or DHCP state. No churn on a live household network. This is what a cautious user gets by running `lankit test-failsafe` with no flags, matching TESTING.md Phase 4's "rehearse under low stakes" framing.
  - **`--full` flag:** builds and uploads the *actual* `revert.py`-built bundle for the current known-good archive; the on-event imports the real `lankit-revert.rsc`; phases assert the sentinel was removed **by the real bundle import**, expected tags are still present afterwards (the rebuild ran), and both the `/log` markers and the durable fired-marker (§7.2 amendment) exist. This is a genuine remove+rebuild of lankit state — brief WiFi/DHCP churn on whatever router it targets — and requires an explicit `--full` acknowledgment. Intended for the G1.13 lab router, or a user who wants true end-to-end certification and accepts the blip.
  - Docstring for both modes must state plainly which one is running and what it does/doesn't touch.
- **G3.7 — fasttrack:** must follow §5.3's constraint (untagged disable; remove-pass re-enables).
- **G2.2 — copy:** rollback card and docs rewritten around: wired .88 is the mid-revert survival path; "restore X" is convergence; after a failsafe fire, run `lankit probe`.

## 13. Sign-off record (2026-07-07)

Reviewed independently against the actual templates and code (not just the design's own claims) before sign-off: the §5.1 tag registry and §5.2 removal order were verified line-by-line against all seven `.rsc.j2` templates with zero discrepancies found; the master-WiFi live-capture logic (§5.3) was traced for a suspected apply-to-apply inconsistency and confirmed correct (the hidden-master transformation is a constant, not something that varies between applies, so capturing live state always captures the right restore target). The design's cross-check finding on `probe.py`'s incomplete tag inventory was independently confirmed against `probe.py:264-296`.

**Decisions on the original seven open questions:**

1. **Dynamic bridge-VLAN entries:** accepted as written — disable vlan-filtering first (step 2). Availability over isolation mid-revert is correct at this project's safety bar. Confirm the dynamic-entry behavior on hardware in G1.13, but it's not load-bearing for this decision.
2. **RouterOS behavior removing referenced objects:** accepted — not resolvable on paper; the order matches the templates' own proven removal order, and `:do/on-error` is the backstop. G1.13 runbook item.
3. **Master-WiFi capture/restore:** accepted — include in v1, as designed.
4. **Plaintext WiFi passphrases:** accepted, on the finding recorded in §3 — this does not introduce a new class of risk (`ansible/generated/04-wifi.rsc` already carries the same exposure today, unhardened) and the archive's stated permissions are tighter than that existing exposure. Contingent on the retention-sweep mechanism (§8, §12) shipping in G1.2.
5. **Probe inventory migration:** **resolved — in scope for G1.2, not deferred.** Letting `probe.py` stay on its own wrong, hand-maintained lists while a correct registry exists elsewhere is the exact truth-decay pattern the v1-readiness review diagnosed as this project's stall cause; fixing it here and not propagating it would reproduce that pattern inside the fix meant to close it.
6. **Convergence semantics under drift:** accepted as stated — a revert converges declared state, it is not a byte-faithful undo of only the changed slice. G2.2's copy must say so explicitly.
7. **`test-failsafe` drilling a real remove+rebuild:** **not accepted as originally written — revised to a default/`--full` split** (see the amended G1.9 entry in §12). The default stays a zero-churn mechanical drill (real scheduler self-delete/on-event/verification shape, minimal one-line payload); the full production-bundle drill (genuine remove+rebuild, brief live churn) requires an explicit `--full` flag. A safety-rehearsal command that itself risks disrupting a household's network by default is a small violation of this project's own bar.

**Three additional findings from independent review, folded into the design above (not from the original open-questions list):**

- **Failsafe-fire detection needs a durable marker, not just a `/log` scan** (§7.2 amendment) — the in-memory log buffer can rotate out before a long-absent user (Yemi, Clem) next connects, which is exactly the scenario this detection layer exists for.
- **Archive retention needs an enforcement mechanism, not just a stated invariant** (§8, §12 amendments) — `sweep_orphaned_archives()` triggered from the existing snapshot-rotation path.
- **`probe.py` migration is in scope for G1.2** (see decision 5 above) rather than a deferred follow-up.

**Status: signed off. G1.2 may proceed**, incorporating all amendments recorded inline above (§3, §5.1, §7.2, §8, §12).
