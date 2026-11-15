"""
lankit.cli.commands._revert_shared
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Shared pieces of the tag-scoped revert flow used by apply, rollback, and
restore (G1.3/G1.4 — docs/design/tag-scoped-revert.md §7). Not a click
command module: the leading underscore keeps it out of the command
registry in lankit/cli/__main__.py.

classify_import_error and check_failsafe_fired_marker were extracted
mechanically from apply.py (G1.3) so all three revert-capable commands
share one implementation — no behavior change from the inline originals.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from datetime import datetime

    from lankit.core.revert import KnownGoodArchive, RevertVerdict

FAILSAFE_NAME = "lankit-failsafe"
FIRED_MARKER_NAME = "lankit-failsafe-fired"

# The single on-router revert artifact (design §6). One name for every
# caller — apply's failsafe/decline paths, rollback, and restore — so a
# manual re-run instruction is always `/import file=lankit-revert.rsc`.
# (`lankit-restore.rsc` remains the raw-snapshot name reserved for the
# factory-reset additive path, so the two mechanisms are never confused
# on the router's file listing.)
REVERT_BUNDLE_NAME = "lankit-revert.rsc"


def build_failsafe_on_event(import_file: str = REVERT_BUNDLE_NAME,
                            scheduler_name: str = FAILSAFE_NAME,
                            marker_name: str = FIRED_MARKER_NAME) -> str:
    """The failsafe scheduler's on-event script (design §6).

    Self-deleting one-shot: the scheduler removes itself *before* importing,
    so it cannot fire twice even if a cancel is delayed or the session dies
    at an unlucky moment. On completion — success or failure — it leaves the
    durable fired-marker behind, because RouterOS's own /log buffer is too
    small and short-lived to be the only signal a returning user sees
    (design §7.2 amendment).

    Extracted from apply.py in G1.9 so `lankit test-failsafe` drills this
    exact shape rather than a hand-written lookalike: a rehearsal that
    exercises a different script than production proves less than it looks
    like it does. Only `import_file` differs between the two callers.

    Pure — no I/O — so it's unit-testable on its own.
    """
    return (
        f'/system scheduler remove [find name="{scheduler_name}"];'
        f' :log warning "lankit-failsafe: fired - importing {import_file}";'
        f' :do {{'
        f' /import file={import_file};'
        f' :log warning "lankit-failsafe: revert import finished";'
        f' /system scheduler add name="{marker_name}" disabled=yes'
        f' comment="lankit:failsafe-fired:ok"'
        f' }} on-error={{'
        f' :log error "lankit-failsafe: revert import FAILED - run lankit probe";'
        f' /system scheduler add name="{marker_name}" disabled=yes'
        f' comment="lankit:failsafe-fired:error"'
        f' }}'
    )

# RouterOS emits informational lines on stderr in some versions; only treat
# import output as a hard failure on unambiguous error indicators.
_INFO_PATTERNS = (
    "loading file",
    "script file loaded",
    "warning",
)
_ERROR_PATTERNS = (
    "bad command",
    "syntax error",
    "no such item",
    "expected",
    "failure",
    "error",
    "does not match",  # e.g. "input does not match any value of interface" — a real
                        # RouterOS parse error (ENH-025) that none of the patterns
                        # above previously caught; found while writing G1.3's tests.
)


def classify_import_error(err: str) -> bool:
    """True if this stderr text from a RouterOS /import is a hard error,
    not one of the informational messages RouterOS sometimes emits on
    stderr. Pure — no I/O — so it's unit-testable on its own."""
    if not err:
        return False
    err_lower = err.lower()
    is_info = any(p in err_lower for p in _INFO_PATTERNS)
    is_error = any(p in err_lower for p in _ERROR_PATTERNS)
    return is_error and not is_info


def import_failure(out: str, err: str) -> Optional[str]:
    """The error text if a RouterOS `/import` failed, else None.

    RouterOS 7.x reports import failures on *stdout* over SSH exec, with
    stderr empty: `Script Error: failure: already have such address
    (/ip/address/add; line 13) (:import; line 1)`. Only checking stderr made
    every failed import look like a success (#66). `Script Error:` is the
    prefix RouterOS uses for every /import halt, and is never informational,
    so it is fatal on its own; stderr keeps the existing classifier. Pure."""
    for line in (out or "").splitlines():
        if "script error:" in line.lower():
            return line.strip()
    if classify_import_error(err):
        return err.strip()
    return None


MIN_ROUTEROS = (7, 21)


def parse_routeros_version(text: str) -> Optional[tuple[int, int]]:
    """(major, minor) from `RouterConnection.version()` output such as
    `7.24.2 (stable)`, or None if it can't be read. Pure."""
    import re
    m = re.match(r"\s*(\d+)\.(\d+)", text or "")
    return (int(m.group(1)), int(m.group(2))) if m else None


def preflight_router(conn, console, version: Optional[str] = None) -> None:
    """Refuse routers lankit can't safely change. Call right after connecting,
    before touching anything. Raises SystemExit(1).

    - RouterOS older than 7.21, or unreadable: the templates rely on 7.21
      defaults (fasttrack hw-offload, #63) and 7.x output formats.
    """
    ver_text = version if version is not None else conn.version()
    ver = parse_routeros_version(ver_text)
    if ver is None or ver < MIN_ROUTEROS:
        console.print(
            f"[bold red]Refusing: RouterOS {ver_text or 'unknown'} is not supported.[/bold red]\n"
            f"  lankit needs RouterOS {MIN_ROUTEROS[0]}.{MIN_ROUTEROS[1]} or newer. "
            "Upgrade the router first."
        )
        raise SystemExit(1)



def check_failsafe_fired_marker(conn, console) -> None:
    """Durable, log-rotation-independent signal that a failsafe fired since
    the last successful 'keep'. Checked on every connect, before touching
    anything else — the /log line this is a backstop for can scroll out of
    RouterOS's small buffer long before a Yemi/Clem-style returning user
    next connects (design §7.2, sign-off amendment)."""
    from rich.prompt import Confirm

    out, err = conn.run_tolerant(f'/system scheduler print detail where name="{FIRED_MARKER_NAME}"')
    if err or FIRED_MARKER_NAME not in out:
        return

    status = "error" if "failsafe-fired:error" in out else "ok" if "failsafe-fired:ok" in out else "unknown"
    console.print(
        f"[bold red]⚠ A lankit failsafe fired previously[/bold red] and was not cleared "
        f"(recorded status: {status}). The router may have reverted unexpectedly since "
        f"the last confirmed apply."
    )
    log_out, _ = conn.run_tolerant('/log print where message~"lankit-failsafe" without-paging')
    if log_out.strip():
        console.print("  Recent related log lines:")
        for line in log_out.strip().splitlines()[-5:]:
            console.print(f"    [dim]{line.strip()}[/dim]")
    console.print("  Run [bold]lankit probe[/bold] to check current state before trusting it.")
    if not Confirm.ask("Continue anyway?", default=False):
        raise SystemExit(1)


def current_archive_id(router_ip: str) -> Optional[str]:
    """The known-good archive id that is current for this router right now,
    or None if no archive exists yet. Snapshots saved outside apply's keep
    path (pre-apply, pre-rollback, pre-restore, commit) pair to this —
    design §8: they capture the state the *current* archive describes."""
    from lankit.core import revert
    ids = revert.list_known_good_ids(router_ip)
    return ids[-1] if ids else None


def resolve_paired_archive(router_ip: str, snapshot_path: Path) -> tuple[str, Optional["KnownGoodArchive"]]:
    """Resolve a target snapshot to its paired known-good archive (design §8).

    Returns (status, archive):
      ("paired", archive)        — pairing recorded and archive loadable
      ("unpaired", None)         — no pairing recorded: the snapshot predates
                                   G1.2's archive store, or isn't in the index
                                   at all (e.g. an arbitrary file path)
      ("archive-missing", None)  — pairing recorded but the archive directory
                                   is gone from disk (should not happen while
                                   the pairing survives — retention keeps
                                   paired archives — but never silently
                                   downgraded if it does)
    """
    from lankit.core import revert, snapshots
    archive_id = snapshots.archive_id_for(router_ip, Path(snapshot_path))
    if archive_id is None:
        return "unpaired", None
    archive = revert.load_known_good(router_ip, archive_id)
    if archive is None:
        return "archive-missing", None
    return "paired", archive


def format_age(captured: "datetime", now: Optional["datetime"] = None) -> str:
    """Human-readable age of a snapshot ("5 months ago"), ENH-008 (#16).

    Deliberately coarse and never precise-looking: the question a returning
    user is asking is "is this from before or after the thing I broke?",
    not "how many seconds"."""
    from datetime import datetime, timezone

    now = now or datetime.now(timezone.utc)
    secs = (now - captured).total_seconds()
    if secs < 0:
        return "just now"  # clock skew — never print a negative age
    mins, hours, days = secs / 60, secs / 3600, secs / 86400
    if secs < 90:
        return "just now"
    if mins < 90:
        return f"{int(round(mins))} minutes ago"
    if hours < 36:
        return f"{int(round(hours))} hours ago"
    if days < 45:
        return f"{int(round(days))} days ago"
    if days < 365:
        return f"{int(round(days / 30))} months ago"
    years = days / 365
    return "1 year ago" if round(years) == 1 else f"{int(round(years))} years ago"


def describe_label(label: str) -> str:
    """Plain-English meaning of a snapshot label (ENH-008 #16: "a returning
    user has no idea ... what `pre-apply` means")."""
    if label.startswith("pre-apply-"):
        seg = label[len("pre-apply-"):]
        return f"saved automatically just before a '{seg}' segment apply, capturing the state before those changes"
    if label == "pre-apply":
        return "saved automatically just before a lankit apply, capturing the state before those changes"
    if label.startswith("post-apply-"):
        seg = label[len("post-apply-"):]
        return f"the state a completed '{seg}' segment apply produced"
    if label == "post-apply":
        return "the state a completed lankit apply produced"
    if label == "pre-rollback":
        return "saved automatically just before a lankit rollback, so the rollback itself can be undone"
    if label == "pre-restore":
        return "saved automatically just before a lankit restore, so the restore itself can be undone"
    if label == "commit":
        return "marked by hand with 'lankit commit' as a known-good point"
    return "saved by hand with 'lankit snapshots --save'"


def print_snapshot_context(console, snapshot, status: str, archive, *,
                           heading: str, extra_lines: Optional[list[str]] = None) -> None:
    """The pre-confirmation context block ENH-008 (#16) asks for: age,
    label + what the label means, size, and what the target actually
    restores (its paired known-good archive, or an honest unpaired note).

    Shown before the prompt by both rollback and restore so the user can
    tell WHAT they are about to converge to — not just a filename."""
    when = snapshot.timestamp.strftime("%Y-%m-%d %H:%M UTC")
    console.print(f"\n{heading}")
    console.print(f"  File:     [bold]{snapshot.path.name}[/bold]")
    console.print(f"  Captured: {format_age(snapshot.timestamp)}  [dim]({when})[/dim]")
    console.print(f"  Label:    {snapshot.label}  [dim]— {describe_label(snapshot.label)}[/dim]")
    console.print(f"  Size:     {snapshot.size_kb} KB")

    if status == "paired" and archive is not None:
        m = archive.manifest
        console.print(
            f"  Restores: known-good archive [dim]{m.archive_id}[/dim] — "
            f"{len(m.expected_tags)} lankit resource(s); "
            f"segments: {', '.join(m.segments_applied) or 'none'}; "
            f"globals: {'yes' if m.globals_applied else 'no'}"
        )
    elif status == "archive-missing":
        console.print("  Restores: [bold red]nothing — its paired known-good archive "
                      "is missing from disk[/bold red]")
    else:
        console.print("  Restores: [yellow]unpaired (legacy)[/yellow] — no known-good archive "
                      "was recorded when this snapshot was taken")

    for line in extra_lines or []:
        console.print(f"  {line}")


def upload_revert_bundle(conn, bundle: str, console) -> bool:
    """Upload the revert bundle and verify it landed with exactly the
    expected byte size (the same verified-upload approach apply uses before
    arming the failsafe — design §6). Returns False if unverified; the
    caller must then abort before importing anything."""
    bundle_size = len(bundle.encode("utf-8"))
    conn.upload(bundle, REVERT_BUNDLE_NAME)
    return conn.verify_uploaded_file(REVERT_BUNDLE_NAME, bundle_size)


def import_revert_bundle_and_verify(conn, expected_tags, console) -> tuple[bool, "RevertVerdict"]:
    """Import the already-uploaded revert bundle over the live SSH session,
    read live tags back, and verify convergence against expected_tags.

    Returns (import_clean, verdict). Prints stderr excerpts as they occur;
    the success/failure headline is the caller's job — each caller has its
    own §7.2 wording — and design §7.1's global invariant applies: a green
    mark only when import_clean AND verdict.ok, anything else is a bold red
    failure with evidence and exit 1."""
    from lankit.core import revert

    out, err = conn.run_tolerant(f"/import file={REVERT_BUNDLE_NAME}")
    failure = import_failure(out, err)
    import_clean = failure is None
    if failure:
        console.print(f"  [red]Import error:[/red] {failure}")
    elif err:
        console.print(f"  [yellow]Warning:[/yellow] {err.strip()}")

    live_tags = conn.read_lankit_tags()
    verdict = revert.verify_revert(live_tags, set(expected_tags))
    return import_clean, verdict


def print_verdict_evidence(verdict: "RevertVerdict", console) -> None:
    """Print the missing/unexpected tag evidence lists for a failed revert
    verification (extracted verbatim from apply's G1.3 failure output, plus
    the legacy-tag note below, so all three callers show identical
    evidence)."""
    if verdict.missing:
        shown = sorted(verdict.missing)[:10]
        console.print(f"  Missing ({len(verdict.missing)}): expected but not found on the router.")
        for t in shown:
            console.print(f"    [red]✗[/red] {t}")
        if len(verdict.missing) > len(shown):
            console.print(f"    ... and {len(verdict.missing) - len(shown)} more")
    if verdict.unexpected:
        shown = sorted(verdict.unexpected)[:10]
        console.print(f"  Unexpected ({len(verdict.unexpected)}): still present, should be gone.")
        for t in shown:
            console.print(f"    [red]✗[/red] {t}")
        if len(verdict.unexpected) > len(shown):
            console.print(f"    ... and {len(verdict.unexpected) - len(shown)} more")


def prompt_unpaired_snapshot_path(target_path: Path, status: str, cfg, console) -> str:
    """The honest-refusal path for a snapshot with no usable paired archive
    (design §9). Returns "remove-only" if the user explicitly accepts the
    remove-pass-only revert, or "declined" after printing the factory-reset
    + additive-import instructions. Never pretends a tag-scoped restore of
    the snapshot is possible, and never auto-accepts (a --yes flag must not
    silently pick this downgrade — the prompt is always asked)."""
    from rich.prompt import Confirm

    if status == "archive-missing":
        console.print(
            f"[bold red]The known-good archive paired with "
            f"{target_path.name} is missing from disk.[/bold red]"
        )
    else:
        console.print(
            f"[yellow]{target_path.name} has no paired known-good archive[/yellow] — "
            f"it predates lankit's revert-safety upgrade, or was saved outside "
            f"lankit's snapshot index."
        )
    console.print(
        "A tag-scoped restore of that snapshot's state is not possible: raw "
        "/export snapshots can't be rebuilt from directly (they omit WiFi "
        "passphrases and lankit's untagged settings), and importing one over "
        "a live config cannot remove anything. Two honest options:"
    )
    console.print(
        "  [bold](a)[/bold] Remove-only revert — strip everything lankit owns; the router "
        "converges to defconf plus\n      any manual config. The flat wired "
        "192.168.88.0/24 network stays up; lankit-created\n      segments, WiFi networks, "
        "and DHCP servers are removed."
    )
    console.print(
        "  [bold](b)[/bold] Factory reset + additive import of the snapshot — the one case "
        "where importing a raw\n      snapshot works, because the config is near-empty. "
        "Instructions printed if you decline (a)."
    )
    if Confirm.ask("Proceed with the remove-only revert (option a)?", default=False):
        return "remove-only"

    console.print("\nFactory-reset + additive-import path (option b):")
    console.print("  1. Factory-reset the router — see the rollback card "
                  "([bold]lankit rollback-card[/bold]) for the exact steps.")
    console.print("  2. Reconnect over wired ethernet on the default 192.168.88.0/24 network.")
    console.print("  3. Upload and import the snapshot:")
    console.print(f"       scp {target_path} {cfg.router.ssh_user}@192.168.88.1:lankit-restore.rsc")
    console.print(f"       ssh {cfg.router.ssh_user}@192.168.88.1 '/import file=lankit-restore.rsc'")
    console.print("[dim]No changes were made by this command.[/dim]")
    return "declined"


def execute_revert_operation(
    cfg, target_path: Path, archive, console, *,
    snap_label: str,
    success_line: str,
    failure_line: str,
    escalation_lines: list[str],
) -> None:
    """The shared rollback/restore live flow (design §7.2 rows for both):
    connect, check the durable failsafe-fired marker, save a paired
    safety snapshot of the current state, build + upload (verified) +
    import the revert bundle for `archive`, read live tags back, verify.

    archive=None is the explicitly-accepted remove-only path (design §9) —
    verified against an empty expected set, and reported honestly as a
    remove-only revert, never as a restore of the target snapshot.

    Prints `success_line` only when import stderr was clean AND
    verification passed (design §7.1's global invariant). Any other
    outcome prints `failure_line`, the evidence lists, next steps, and
    raises SystemExit(1). Router-level failures also exit 1.
    """
    from lankit.core import revert, snapshots
    from lankit.core.router import RouterConnection, RouterError

    console.print(f"\nConnecting to [bold]{cfg.router.ip}[/bold]...")
    try:
        with RouterConnection(cfg.router.ip, cfg.router.ssh_user, cfg.ssh_key) as conn:
            check_failsafe_fired_marker(conn, console)
            preflight_router(conn, console)

            # Save the current state first, paired to the archive that is
            # current right now (design §8) — so this operation itself can
            # be undone, and the pairing keeps that archive retained.
            current = conn.export_config()
            saved = snapshots.save(cfg.router.ip, current, label=snap_label,
                                   archive_id=current_archive_id(cfg.router.ip))
            console.print(f"  Saved current config: [dim]{saved.name}[/dim]")

            # Master-WiFi facts for the remove-pass (design §5.3): captured
            # live; fail-open to None (masters stay hidden, wired recovery
            # on 192.168.88.0/24 unaffected).
            master_facts = conn.read_master_wifi_facts()
            if master_facts is None:
                console.print("  [dim]Master WiFi state not captured — the revert would leave "
                              "WiFi masters hidden (wired recovery unaffected).[/dim]")

            # lan_interface for the PVID restore comes from the archive
            # being restored (design §5.3) — the current network.yml may
            # have been edited since; fall back to it only when there is
            # no archive (remove-only path).
            lan_iface = archive.manifest.lan_interface if archive else cfg.router.lan_interface
            bundle = revert.build_revert_bundle(archive, lan_iface, master_facts)

            console.print("  Uploading revert bundle...")
            if not upload_revert_bundle(conn, bundle, console):
                console.print("[bold red]Revert bundle upload could not be verified.[/bold red]")
                console.print("  Refusing to import an unconfirmed file — aborting before "
                              "any changes were made to the router.")
                raise SystemExit(1)

            expected = revert.expected_tags(archive)
            console.print("  Importing revert bundle (remove lankit-owned state, rebuild known-good)...")
            import_clean, verdict = import_revert_bundle_and_verify(conn, expected, console)

            if import_clean and verdict.ok:
                if archive is not None:
                    console.print(success_line)
                else:
                    console.print(
                        "[green]✓[/green] Remove-only revert complete — no lankit-owned "
                        "resources remain (verified). The router is at defconf plus any "
                        f"manual config; this was [bold]not[/bold] a restore of {target_path.name}."
                    )
                return

            console.print(failure_line)
            print_verdict_evidence(verdict, console)
            console.print("\n  Next steps:")
            console.print("    1. Re-run the revert by hand over SSH:")
            console.print(f"       ssh {cfg.router.ssh_user}@{cfg.router.ip} "
                          f"'/import file={REVERT_BUNDLE_NAME}'")
            console.print("    2. Then check: [bold]lankit probe[/bold]")
            for i, line in enumerate(escalation_lines, start=3):
                console.print(f"    {i}. {line}")
            raise SystemExit(1)
    except RouterError as e:
        console.print(f"[bold red]Router error:[/bold red] {e}")
        raise SystemExit(1)
