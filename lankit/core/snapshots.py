"""
lankit.core.snapshots
~~~~~~~~~~~~~~~~~~~~~

Local RouterOS config snapshot management.
Snapshots are full /export verbose outputs stored in ~/.lankit/snapshots/.
An index file per router tracks the ordered history.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

_SNAPSHOTS_DIR = Path.home() / ".lankit" / "snapshots"
_MAX_SNAPSHOTS = 10
_TS_RE = re.compile(r'(\d{8}T\d{6}Z)-(.+)\.rsc$')

# Labels rollback must never pick as a target (G1.5 / review finding S2).
# `pre-rollback` and `pre-restore` are rollback's/restore's own safety
# markers; `post-apply` is the state an apply *produced* — the very state a
# user running rollback is trying to leave. Selecting any of them is how the
# old `snaps[-2]` rule oscillated between a good and a bad state.
_OPERATIONAL_PREFIXES = ("pre-rollback", "pre-restore", "post-apply")

# What rollback walks backward through: the state captured immediately
# before an apply. Prefix-matched so `pre-apply-<segment>` counts too.
_ROLLBACK_TARGET_PREFIX = "pre-apply"


@dataclass
class Snapshot:
    path: Path
    router_ip: str
    timestamp: datetime
    label: str
    archive_id: Optional[str] = None  # paired known-good archive (revert.py), if any
    pinned: bool = False              # exempt from rotation (G1.8)

    @property
    def size_kb(self) -> int:
        return self.path.stat().st_size // 1024


def save(router_ip: str, config_export: str, label: str = "apply",
          archive_id: Optional[str] = None) -> Path:
    """Save a config export snapshot and return its path.

    archive_id: the lankit.core.revert known-good archive id current at
    capture time, if any (design §8's pairing — "restore snapshot X" means
    converge to the lankit state that was live when X was captured, i.e.
    the archive it's paired to, not a raw import of X itself). Wiring
    callers to pass this is G1.3/G1.4's job, not this one's — the default
    of None keeps every existing call site unchanged.
    """
    _SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_ip = router_ip.replace(".", "-")
    path = _SNAPSHOTS_DIR / f"{safe_ip}-{ts}-{label}.rsc"
    path.write_text(config_export)
    _update_index(router_ip, path, archive_id)
    return path


def list_snapshots(router_ip: str) -> list[Path]:
    """Return list of snapshot paths for this router, oldest-first."""
    index = _read_index(router_ip)
    return [Path(e["path"]) for e in index if Path(e["path"]).exists()]


def archive_id_for(router_ip: str, path: Path) -> Optional[str]:
    """The known-good archive id paired with a given snapshot path, if any.

    Returns None both when the snapshot has no pairing (pre-dates the
    archive store, or was never paired) and when the snapshot isn't in the
    index at all — callers that need to distinguish those cases should
    check list_snapshots() membership themselves (design §9: an unpaired
    snapshot gets an honest refusal path, not a silent downgrade)."""
    for e in _read_index(router_ip):
        if Path(e["path"]) == path:
            return e["archive_id"]
    return None


def list_metadata(router_ip: str) -> list[Snapshot]:
    """Return Snapshot objects with parsed metadata, oldest-first."""
    entries = {e["path"]: e for e in _read_index(router_ip)}
    result = []
    for path in list_snapshots(router_ip):
        m = _TS_RE.search(path.name)
        if m:
            ts = datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            label = m.group(2)
        else:
            ts = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            label = path.stem
        entry = entries.get(str(path), {})
        result.append(Snapshot(
            path=path, router_ip=router_ip, timestamp=ts, label=label,
            archive_id=entry.get("archive_id"),
            pinned=bool(entry.get("pinned")),
        ))
    return result


def delete(router_ip: str, path: Path) -> None:
    """Delete a snapshot and remove it from the index."""
    path.unlink(missing_ok=True)
    index = [e for e in _read_index(router_ip) if Path(e["path"]) != path]
    _write_index(router_ip, index)


def latest(router_ip: str) -> Optional[Path]:
    """Return the most recent snapshot path, or None."""
    snaps = list_snapshots(router_ip)
    return snaps[-1] if snaps else None


def previous(router_ip: str) -> Optional[Path]:
    """Return the snapshot before the latest, or None.

    No longer rollback's target rule — see select_rollback_target() and
    review finding S2 for why positional selection was wrong. Kept as a
    generic accessor."""
    snaps = list_snapshots(router_ip)
    if len(snaps) >= 2:
        return snaps[-2]
    return snaps[0] if snaps else None


@dataclass
class RollbackTarget:
    """The snapshot `lankit rollback` should converge to, plus the context
    ENH-008 (#16) asks to be shown before the confirmation prompt."""
    snapshot: Snapshot
    depth: int            # 0 = first rollback since the last apply, 1 = second, ...
    at_oldest: bool       # depth is clamped: no older candidate exists
    candidate_count: int  # how many candidates are in the walk
    fallback: bool        # no pre-apply snapshots at all; walking generic ones


def select_rollback_target(router_ip: str) -> Optional[RollbackTarget]:
    """Pick rollback's target: walk backward through `pre-apply` snapshots.

    Review finding S2: the old rule (`snaps[-2]`) sent a *second* rollback
    forward onto the `post-apply` state the first rollback had just undone,
    so a worried user oscillated between good and bad. Here, rollback depth
    is read off what rollback itself already leaves on disk — its
    `pre-rollback` markers — with no extra state file:

      depth = number of `pre-rollback` snapshots saved *after* the newest
              candidate; target = candidates[-1 - depth]

    So the first rollback after an apply lands on that apply's `pre-apply`
    snapshot, a second lands on the `pre-apply` before it, and so on —
    strictly older every time. A new apply writes a new `pre-apply`, which
    resets the depth to 0 (rollback again means "undo that apply").

    Depth is clamped at the oldest candidate: design §8 makes landing on
    the same target twice idempotent ("restore X" is convergence, not a
    replay), so the honest behavior at the floor is to stay there and say
    so — never to wrap around to something newer.

    Returns None when there is nothing to roll back to. If a router has no
    `pre-apply` snapshots at all (e.g. only `lankit commit` marks), the
    same walk runs over every non-operational snapshot and `fallback` is
    set, so rollback still works instead of refusing outright.
    """
    snaps = list_metadata(router_ip)
    idxs = [i for i, s in enumerate(snaps) if s.label.startswith(_ROLLBACK_TARGET_PREFIX)]
    fallback = False
    if not idxs:
        idxs = [i for i, s in enumerate(snaps)
                if not s.label.startswith(_OPERATIONAL_PREFIXES)]
        fallback = True
    if not idxs:
        return None

    depth = sum(1 for s in snaps[idxs[-1] + 1:] if s.label.startswith("pre-rollback"))
    position = len(idxs) - 1 - depth
    at_oldest = position <= 0
    chosen = snaps[idxs[max(position, 0)]]
    return RollbackTarget(
        snapshot=chosen,
        depth=depth,
        at_oldest=at_oldest,
        candidate_count=len(idxs),
        fallback=fallback,
    )


# ── Rotation and pinning (G1.8, issue #30) ───────────────────────────────────
#
# The cap used to be mentioned only in `lankit snapshots --help`, and every
# apply writes two snapshots — so five applies silently rotated out the state
# a user might have been counting on. Rotation is now something the user can
# see coming (rotation_status) and opt a specific snapshot out of (pin).


@dataclass
class RotationStatus:
    """What rotation is about to do to this router's snapshot history."""
    total: int
    pinned_count: int
    unpinned_count: int
    max_unpinned: int
    oldest: Optional[Snapshot]              # oldest *surviving* snapshot, pinned or not
    next_to_rotate: Optional[Snapshot]      # what the next save would push out, if anything

    @property
    def at_limit(self) -> bool:
        """The next save will rotate something out."""
        return self.unpinned_count >= self.max_unpinned


def _rotate(index: list[dict], max_unpinned: int = _MAX_SNAPSHOTS) -> list[dict]:
    """Trim an index to the newest `max_unpinned` unpinned entries, keeping
    every pinned entry regardless of age.

    The cap therefore bounds *automatic* history only: pinning raises the
    effective total rather than evicting something else to make room. That
    keeps pinning predictable (a pin never costs you another snapshot) and
    keeps a router with 10 pins from immediately rotating out the pre-apply
    snapshot it just wrote — the one the current run may need.

    Pure — no I/O — so it's unit-testable on its own.
    """
    unpinned_positions = [i for i, e in enumerate(index) if not e.get("pinned")]
    excess = len(unpinned_positions) - max_unpinned
    if excess <= 0:
        return list(index)
    dropped = set(unpinned_positions[:excess])
    return [e for i, e in enumerate(index) if i not in dropped]


def rotation_status(router_ip: str, max_unpinned: int = _MAX_SNAPSHOTS) -> RotationStatus:
    """Describe this router's snapshot rotation state — how full it is, what
    the oldest surviving snapshot is, and what the next save would evict."""
    snaps = list_metadata(router_ip)
    unpinned = [s for s in snaps if not s.pinned]
    return RotationStatus(
        total=len(snaps),
        pinned_count=len(snaps) - len(unpinned),
        unpinned_count=len(unpinned),
        max_unpinned=max_unpinned,
        oldest=snaps[0] if snaps else None,
        next_to_rotate=unpinned[0] if len(unpinned) >= max_unpinned else None,
    )


def set_pinned(router_ip: str, path: Path, pinned: bool) -> bool:
    """Pin or unpin a snapshot, exempting it from (or returning it to)
    rotation. Returns True if the index entry was found and changed.

    Pinning does not copy or move the file — it only tells rotation to leave
    this entry alone. `lankit snapshots --delete` still removes a pinned
    snapshot: pinning guards against the automatic sweep, not against an
    explicit instruction.
    """
    index = _read_index(router_ip)
    for e in index:
        if Path(e["path"]) == Path(path):
            if bool(e.get("pinned")) == pinned:
                return False
            e["pinned"] = pinned
            _write_index(router_ip, index)
            return True
    return False


def repair_pairing(router_ip: str, path: Path, archive_id: str) -> bool:
    """Set the paired archive id of an already-saved snapshot (G1.5).

    Only fills a *missing* pairing — an existing one is never overwritten,
    so this can't silently rewrite history. Exists for one case: apply
    saves its pre-apply snapshot before the migration bootstrap runs, so on
    a bootstrapped router that snapshot would be stranded unpaired even
    though an archive describing exactly its state now exists (design §8's
    pairing rule). Returns True if a pairing was written."""
    index = _read_index(router_ip)
    for e in index:
        if Path(e["path"]) == Path(path) and e["archive_id"] is None:
            e["archive_id"] = archive_id
            _write_index(router_ip, index)
            return True
    return False


def _read_index(router_ip: str) -> list[dict]:
    """Read the index, normalized to [{"path": str, "archive_id": str|None}, ...].

    Older index files store bare path strings (pre-dates archive pairing) —
    those are normalized to archive_id=None on read so every caller only
    ever deals with one shape."""
    index_path = _index_path(router_ip)
    if not index_path.exists():
        return []
    try:
        raw = json.loads(index_path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
    return [
        {"path": e, "archive_id": None, "pinned": False} if isinstance(e, str)
        else {"path": e["path"], "archive_id": e.get("archive_id"),
              "pinned": bool(e.get("pinned", False))}
        for e in raw
    ]


def _write_index(router_ip: str, index: list[dict]) -> None:
    """Write the index and sweep any known-good archive no longer paired to
    a surviving entry (sign-off amendment to design §8: retention is
    enforced here, not just stated). Sweeping on every write — not only on
    rotation — makes it self-healing regardless of which code path changed
    the index."""
    _index_path(router_ip).write_text(json.dumps(index, indent=2))
    still_paired = {e["archive_id"] for e in index if e["archive_id"]}
    from lankit.core import revert as _revert  # local: avoid a hard top-level dependency
    _revert.sweep_orphaned_archives(router_ip, still_paired)


def _update_index(router_ip: str, path: Path, archive_id: Optional[str] = None) -> None:
    index = _read_index(router_ip)
    index.append({"path": str(path), "archive_id": archive_id, "pinned": False})
    index = _rotate(index)
    _write_index(router_ip, index)


def _index_path(router_ip: str) -> Path:
    safe_ip = router_ip.replace(".", "-")
    return _SNAPSHOTS_DIR / f"{safe_ip}-index.json"
