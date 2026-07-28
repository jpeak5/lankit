"""
Unit tests for lankit.core.snapshots — save, index, metadata parsing,
latest/previous semantics, rotation at the cap, and delete.

_SNAPSHOTS_DIR is monkeypatched to tmp_path so nothing touches
~/.lankit/snapshots/. Timestamps are made deterministic by patching
the module's datetime with a fake whose now() advances one minute
per call.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

import lankit.core.revert as revert
import lankit.core.snapshots as snapshots

ROUTER = "192.168.88.1"
SAFE = "192-168-88-1"


@pytest.fixture(autouse=True)
def snapdir(tmp_path, monkeypatch):
    snap_dir = tmp_path / "snapshots"
    snap_dir.mkdir()
    monkeypatch.setattr(snapshots, "_SNAPSHOTS_DIR", snap_dir)
    monkeypatch.setattr(revert, "_KNOWN_GOOD_DIR", tmp_path / "known-good")
    return snap_dir


@pytest.fixture
def fake_clock(monkeypatch):
    """Replace snapshots.datetime with a subclass whose now() advances
    one minute per call, starting at a fixed instant. strptime and
    fromtimestamp are inherited unchanged."""
    start = datetime(2026, 7, 6, 12, 0, 0, tzinfo=timezone.utc)

    class FakeDatetime(datetime):
        _calls = 0

        @classmethod
        def now(cls, tz=None):
            value = start + timedelta(minutes=cls._calls)
            cls._calls += 1
            return value

    monkeypatch.setattr(snapshots, "datetime", FakeDatetime)
    return FakeDatetime


@pytest.fixture
def frozen_clock(monkeypatch):
    """Replace snapshots.datetime with a fake whose now() never advances."""
    instant = datetime(2026, 7, 6, 12, 0, 0, tzinfo=timezone.utc)

    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            return instant

    monkeypatch.setattr(snapshots, "datetime", FrozenDatetime)
    return FrozenDatetime


# ─── save ─────────────────────────────────────────────────────────────────────

def test_save_creates_file_and_index(snapdir, fake_clock):
    path = snapshots.save(ROUTER, "# fake export\n", label="apply")

    assert path.exists()
    assert path.parent == snapdir
    assert path.name == f"{SAFE}-20260706T120000Z-apply.rsc"
    assert path.read_text() == "# fake export\n"

    index = snapdir / f"{SAFE}-index.json"
    assert index.exists()
    assert str(path) in index.read_text()


def test_save_default_label_is_apply(snapdir, fake_clock):
    path = snapshots.save(ROUTER, "x")
    assert path.name.endswith("-apply.rsc")


# ─── list / metadata ─────────────────────────────────────────────────────────

def test_list_snapshots_oldest_first(fake_clock):
    p1 = snapshots.save(ROUTER, "one", label="first")
    p2 = snapshots.save(ROUTER, "two", label="second")
    p3 = snapshots.save(ROUTER, "three", label="third")
    assert snapshots.list_snapshots(ROUTER) == [p1, p2, p3]


def test_list_snapshots_skips_missing_files(fake_clock):
    p1 = snapshots.save(ROUTER, "one", label="first")
    p2 = snapshots.save(ROUTER, "two", label="second")
    p1.unlink()
    assert snapshots.list_snapshots(ROUTER) == [p2]


def test_list_snapshots_empty_for_unknown_router():
    assert snapshots.list_snapshots("10.99.99.99") == []


def test_list_metadata_parses_timestamp_and_label(fake_clock):
    snapshots.save(ROUTER, "one", label="pre-apply")
    snapshots.save(ROUTER, "two", label="rollback")

    meta = snapshots.list_metadata(ROUTER)
    assert [m.label for m in meta] == ["pre-apply", "rollback"]
    assert meta[0].timestamp == datetime(2026, 7, 6, 12, 0, 0, tzinfo=timezone.utc)
    assert meta[1].timestamp == datetime(2026, 7, 6, 12, 1, 0, tzinfo=timezone.utc)
    assert all(m.router_ip == ROUTER for m in meta)
    assert meta[0].timestamp.tzinfo is timezone.utc


def test_corrupt_index_treated_as_empty(snapdir):
    (snapdir / f"{SAFE}-index.json").write_text("{not json")
    assert snapshots.list_snapshots(ROUTER) == []
    assert snapshots.latest(ROUTER) is None


# ─── latest / previous ───────────────────────────────────────────────────────

def test_latest_and_previous(fake_clock):
    assert snapshots.latest(ROUTER) is None
    assert snapshots.previous(ROUTER) is None

    p1 = snapshots.save(ROUTER, "one", label="first")
    # With a single snapshot, previous falls back to that snapshot
    # (documented behaviour in snapshots.previous, though its docstring
    # says "or None" — see report; rollback target still exists this way).
    assert snapshots.latest(ROUTER) == p1
    assert snapshots.previous(ROUTER) == p1

    p2 = snapshots.save(ROUTER, "two", label="second")
    p3 = snapshots.save(ROUTER, "three", label="third")
    assert snapshots.latest(ROUTER) == p3
    assert snapshots.previous(ROUTER) == p2


# ─── rotation ────────────────────────────────────────────────────────────────

def test_rotation_caps_index_at_ten(fake_clock):
    paths = [
        snapshots.save(ROUTER, f"export {i}", label=f"s{i:02d}")
        for i in range(12)
    ]

    listed = snapshots.list_snapshots(ROUTER)
    assert len(listed) == 10
    # Oldest two dropped from the index, newest ten kept in order
    assert listed == paths[2:]
    assert paths[0] not in listed
    assert paths[1] not in listed
    # Rotation trims the index only; the files themselves are not deleted
    assert paths[0].exists()


def test_indexes_are_per_router(fake_clock):
    p_a = snapshots.save("10.0.0.1", "a")
    p_b = snapshots.save("10.0.0.2", "b")
    assert snapshots.list_snapshots("10.0.0.1") == [p_a]
    assert snapshots.list_snapshots("10.0.0.2") == [p_b]


# ─── delete ──────────────────────────────────────────────────────────────────

def test_delete_removes_file_and_index_entry(fake_clock):
    p1 = snapshots.save(ROUTER, "one", label="first")
    p2 = snapshots.save(ROUTER, "two", label="second")

    snapshots.delete(ROUTER, p2)
    assert not p2.exists()
    assert snapshots.list_snapshots(ROUTER) == [p1]
    assert snapshots.latest(ROUTER) == p1


def test_delete_missing_file_is_tolerated(fake_clock):
    p1 = snapshots.save(ROUTER, "one", label="first")
    p1.unlink()
    snapshots.delete(ROUTER, p1)  # must not raise
    assert snapshots.list_snapshots(ROUTER) == []


# ─── known limitation ────────────────────────────────────────────────────────

@pytest.mark.xfail(
    strict=True,
    reason="save() timestamps have one-second resolution: two saves with the "
    "same label in the same second write the same filename (second overwrites "
    "first) and append a duplicate index entry. Genuine bug in "
    "lankit.core.snapshots.save — not fixed here per unit scope.",
)
def test_same_second_saves_do_not_collide(frozen_clock):
    p1 = snapshots.save(ROUTER, "one", label="apply")
    p2 = snapshots.save(ROUTER, "two", label="apply")
    listed = snapshots.list_snapshots(ROUTER)
    assert len(set(listed)) == 2, "both snapshots should be retrievable"
    assert p1 != p2
    assert p1.read_text() == "one", "first snapshot content should survive"


# ─── known-good archive pairing (G1.2, design §8) ────────────────────────────

def _make_archive(router_ip=ROUTER, hash_="h1"):
    return revert.save_known_good(
        router_ip, {"01-vlans.rsc": "x\n"},
        segments_applied=["trusted"], globals_applied=True,
        network_yml_hash=hash_, lan_interface="ether2",
    )


def test_save_with_archive_id_is_retrievable_via_archive_id_for(fake_clock):
    aid = _make_archive()
    p = snapshots.save(ROUTER, "export", label="pre-apply", archive_id=aid)
    assert snapshots.archive_id_for(ROUTER, p) == aid


def test_save_without_archive_id_pairs_to_none(fake_clock):
    p = snapshots.save(ROUTER, "export", label="apply")
    assert snapshots.archive_id_for(ROUTER, p) is None


def test_list_metadata_exposes_archive_id(fake_clock):
    aid = _make_archive()
    snapshots.save(ROUTER, "export", label="pre-apply", archive_id=aid)
    snapshots.save(ROUTER, "export2", label="post-apply")
    meta = snapshots.list_metadata(ROUTER)
    assert meta[0].archive_id == aid
    assert meta[1].archive_id is None


def test_legacy_flat_string_index_entries_read_as_unpaired(snapdir):
    # Index files predating archive pairing store bare path strings.
    legacy_path = snapdir / "legacy-snap.rsc"
    legacy_path.write_text("legacy content")
    (snapdir / f"{SAFE}-index.json").write_text(json.dumps([str(legacy_path)]))

    assert snapshots.list_snapshots(ROUTER) == [legacy_path]
    assert snapshots.archive_id_for(ROUTER, legacy_path) is None
    meta = snapshots.list_metadata(ROUTER)
    assert meta[0].archive_id is None


def test_legacy_index_upgrades_to_dict_format_on_next_write(snapdir, fake_clock):
    legacy_path = snapdir / "legacy-snap.rsc"
    legacy_path.write_text("legacy content")
    (snapdir / f"{SAFE}-index.json").write_text(json.dumps([str(legacy_path)]))

    snapshots.save(ROUTER, "new export", label="apply")  # triggers a rewrite

    raw = json.loads((snapdir / f"{SAFE}-index.json").read_text())
    assert all(isinstance(e, dict) for e in raw)


def test_delete_sweeps_orphaned_archive(fake_clock):
    # The orphaned archive must not be the most recent one — the latest
    # archive per router is always protected from the sweep (G1.11 review
    # finding; see test_delete_never_sweeps_most_recent_archive).
    import time
    aid = _make_archive(hash_="old")
    time.sleep(0.01)
    aid_current = _make_archive(hash_="current")
    p = snapshots.save(ROUTER, "export", label="pre-apply", archive_id=aid)
    snapshots.save(ROUTER, "export2", label="post-apply", archive_id=aid_current)
    assert revert.list_known_good_ids(ROUTER) == [aid, aid_current]

    snapshots.delete(ROUTER, p)
    assert revert.list_known_good_ids(ROUTER) == [aid_current]


def test_delete_never_sweeps_most_recent_archive(fake_clock):
    # G1.11 review finding: the latest archive is the live revert target
    # the next failsafe builds against — protected even with zero pairings.
    aid = _make_archive()
    p = snapshots.save(ROUTER, "export", label="pre-apply", archive_id=aid)
    snapshots.delete(ROUTER, p)
    assert revert.list_known_good_ids(ROUTER) == [aid]


def test_delete_keeps_archive_still_paired_to_another_snapshot(fake_clock):
    aid = _make_archive()
    p1 = snapshots.save(ROUTER, "export1", label="pre-apply", archive_id=aid)
    snapshots.save(ROUTER, "export2", label="post-apply", archive_id=aid)

    snapshots.delete(ROUTER, p1)
    assert revert.list_known_good_ids(ROUTER) == [aid]


def test_rotation_sweeps_archive_paired_only_to_the_dropped_entry(fake_clock):
    # As above: a newer archive must exist, otherwise the dropped entry's
    # archive is the latest and stays protected regardless of pairing.
    import time
    aid_old = _make_archive(hash_="old")
    time.sleep(0.01)
    aid_current = _make_archive(hash_="current")
    snapshots.save(ROUTER, "export old", label="pre-apply", archive_id=aid_old)

    for i in range(11):
        snapshots.save(ROUTER, f"filler {i}", label=f"s{i:02d}", archive_id=aid_current)

    assert revert.list_known_good_ids(ROUTER) == [aid_current]


def test_rotation_never_sweeps_most_recent_archive(fake_clock):
    # G1.11 review finding, concrete failure path 2: ten routine
    # commit/rollback/restore-style unpaired saves rotate the one paired
    # post-apply snapshot out of the 10-cap index; the current archive
    # (the next failsafe's revert target) must survive anyway.
    aid = _make_archive()
    snapshots.save(ROUTER, "export", label="post-apply", archive_id=aid)

    for i in range(11):
        snapshots.save(ROUTER, f"filler {i}", label=f"s{i:02d}")

    assert revert.list_known_good_ids(ROUTER) == [aid]


def test_rotation_keeps_archive_still_referenced_by_a_surviving_entry(fake_clock):
    aid = _make_archive()
    snapshots.save(ROUTER, "export", label="pre-apply", archive_id=aid)
    # Re-pair the same archive to later snapshots so it survives rotation.
    for i in range(11):
        snapshots.save(ROUTER, f"filler {i}", label=f"s{i:02d}", archive_id=aid)

    assert revert.list_known_good_ids(ROUTER) == [aid]


def test_archives_for_different_routers_are_independent(fake_clock):
    import time
    aid_a = _make_archive(router_ip="10.0.0.1", hash_="a")
    aid_b_old = _make_archive(router_ip="10.0.0.2", hash_="b-old")
    time.sleep(0.01)
    aid_b_new = _make_archive(router_ip="10.0.0.2", hash_="b-new")
    snapshots.save("10.0.0.1", "export", archive_id=aid_a)
    # Router 2's older archive has no paired snapshot — swept the next
    # time *its own* router's index is written to, not before. (The
    # newest archive per router is always kept — G1.11 review finding.)
    assert revert.list_known_good_ids("10.0.0.1") == [aid_a]
    assert revert.list_known_good_ids("10.0.0.2") == [aid_b_old, aid_b_new]
    snapshots.save("10.0.0.2", "export", archive_id=aid_b_new)  # sweeps orphaned aid_b_old
    assert revert.list_known_good_ids("10.0.0.2") == [aid_b_new]


# ─── rollback target selection (G1.5 / review finding S2) ────────────────────

def _labels(router=ROUTER):
    return [s.label for s in snapshots.list_metadata(router)]


def test_rollback_target_is_the_pre_apply_snapshot(fake_clock):
    snapshots.save(ROUTER, "before", label="pre-apply")
    snapshots.save(ROUTER, "after", label="post-apply")

    target = snapshots.select_rollback_target(ROUTER)
    assert target.snapshot.label == "pre-apply"
    assert target.depth == 0
    assert target.candidate_count == 1
    assert target.at_oldest is True
    assert target.fallback is False


def test_rollback_never_selects_post_apply_or_its_own_markers(fake_clock):
    snapshots.save(ROUTER, "p1", label="pre-apply")
    snapshots.save(ROUTER, "q1", label="post-apply")
    snapshots.save(ROUTER, "r1", label="pre-rollback")
    snapshots.save(ROUTER, "s1", label="pre-restore")

    target = snapshots.select_rollback_target(ROUTER)
    assert target.snapshot.label == "pre-apply"


def test_s2_trace_double_rollback_walks_strictly_older(fake_clock):
    """The exact review-finding-S2 trace: apply, apply, then rollback twice.

    Old rule (snaps[-2]) sent the second rollback forward onto post-apply —
    the bad state the first rollback had just undone. It must now land on
    the pre-apply *before* the one it just used."""
    # First apply
    pre1 = snapshots.save(ROUTER, "state0", label="pre-apply")
    snapshots.save(ROUTER, "state1", label="post-apply")
    # Second apply (the one that broke things)
    pre2 = snapshots.save(ROUTER, "state1", label="pre-apply")
    bad = snapshots.save(ROUTER, "broken", label="post-apply")

    first = snapshots.select_rollback_target(ROUTER)
    assert first.snapshot.path == pre2
    assert first.depth == 0

    # rollback saves its own pre-rollback marker of the current (bad) state
    snapshots.save(ROUTER, "broken", label="pre-rollback")

    second = snapshots.select_rollback_target(ROUTER)
    assert second.snapshot.path == pre1
    assert second.depth == 1
    assert second.snapshot.timestamp < first.snapshot.timestamp
    assert second.snapshot.path != bad


def test_rollback_walks_further_back_on_each_invocation(fake_clock):
    pres = []
    for i in range(3):
        pres.append(snapshots.save(ROUTER, f"pre{i}", label="pre-apply"))
        snapshots.save(ROUTER, f"post{i}", label="post-apply")

    selected = []
    for _ in range(3):
        t = snapshots.select_rollback_target(ROUTER)
        selected.append(t.snapshot.path)
        snapshots.save(ROUTER, "current", label="pre-rollback")

    assert selected == [pres[2], pres[1], pres[0]]
    # strictly older each time — never forward
    assert selected == sorted(selected, key=lambda p: p.name, reverse=True)


def test_rollback_depth_clamps_at_the_oldest_candidate(fake_clock):
    oldest = snapshots.save(ROUTER, "pre", label="pre-apply")
    snapshots.save(ROUTER, "post", label="post-apply")
    for _ in range(4):
        snapshots.save(ROUTER, "current", label="pre-rollback")

    target = snapshots.select_rollback_target(ROUTER)
    assert target.snapshot.path == oldest
    assert target.at_oldest is True
    # Idempotent landing (design §8) — never wraps around to something newer
    assert target.depth >= target.candidate_count


def test_new_apply_resets_the_rollback_walk(fake_clock):
    snapshots.save(ROUTER, "pre0", label="pre-apply")
    snapshots.save(ROUTER, "post0", label="post-apply")
    snapshots.save(ROUTER, "current", label="pre-rollback")
    assert snapshots.select_rollback_target(ROUTER).depth == 1

    fresh = snapshots.save(ROUTER, "pre1", label="pre-apply")
    snapshots.save(ROUTER, "post1", label="post-apply")

    target = snapshots.select_rollback_target(ROUTER)
    assert target.snapshot.path == fresh
    assert target.depth == 0


def test_segment_pre_apply_snapshots_are_rollback_targets(fake_clock):
    snapshots.save(ROUTER, "pre", label="pre-apply")
    seg = snapshots.save(ROUTER, "pre-seg", label="pre-apply-iot")
    snapshots.save(ROUTER, "post", label="post-apply-iot")

    assert snapshots.select_rollback_target(ROUTER).snapshot.path == seg


def test_rollback_falls_back_to_non_operational_snapshots(fake_clock):
    # No pre-apply snapshots at all (e.g. only 'lankit commit' marks):
    # rollback still works rather than refusing outright.
    c1 = snapshots.save(ROUTER, "one", label="commit")
    c2 = snapshots.save(ROUTER, "two", label="commit")
    snapshots.save(ROUTER, "cur", label="pre-rollback")

    target = snapshots.select_rollback_target(ROUTER)
    assert target.fallback is True
    assert target.snapshot.path == c1  # depth 1 → one step back from c2
    assert target.candidate_count == 2


def test_rollback_target_none_when_no_snapshots(fake_clock):
    assert snapshots.select_rollback_target(ROUTER) is None


def test_rollback_target_none_when_only_operational_markers(fake_clock):
    snapshots.save(ROUTER, "cur", label="pre-rollback")
    snapshots.save(ROUTER, "cur", label="post-apply")
    assert snapshots.select_rollback_target(ROUTER) is None


def test_rollback_target_carries_archive_pairing(fake_clock):
    aid = _make_archive()
    snapshots.save(ROUTER, "pre", label="pre-apply", archive_id=aid)
    target = snapshots.select_rollback_target(ROUTER)
    assert target.snapshot.archive_id == aid


# ─── repair_pairing (G1.5 secondary: bootstrap-created archive) ──────────────

def test_repair_pairing_fills_a_missing_archive_id(fake_clock):
    p = snapshots.save(ROUTER, "pre", label="pre-apply")  # archive_id=None
    aid = _make_archive()

    assert snapshots.repair_pairing(ROUTER, p, aid) is True
    assert snapshots.archive_id_for(ROUTER, p) == aid


def test_repair_pairing_never_overwrites_an_existing_pairing(fake_clock):
    aid = _make_archive()
    p = snapshots.save(ROUTER, "pre", label="pre-apply", archive_id=aid)

    assert snapshots.repair_pairing(ROUTER, p, "some-other-id") is False
    assert snapshots.archive_id_for(ROUTER, p) == aid


def test_repair_pairing_ignores_unknown_paths(fake_clock, tmp_path):
    assert snapshots.repair_pairing(ROUTER, tmp_path / "nope.rsc", "x") is False


# ─── G1.8: rotation visibility and pinning (issue #30) ───────────────────────

def test_rotate_is_a_noop_below_the_cap():
    index = [{"path": f"p{i}", "archive_id": None, "pinned": False} for i in range(3)]
    assert snapshots._rotate(index, max_unpinned=10) == index


def test_rotate_drops_oldest_unpinned_first():
    index = [{"path": f"p{i}", "archive_id": None, "pinned": False} for i in range(5)]
    kept = snapshots._rotate(index, max_unpinned=3)
    assert [e["path"] for e in kept] == ["p2", "p3", "p4"]


def test_rotate_never_drops_a_pinned_entry():
    index = [{"path": f"p{i}", "archive_id": None, "pinned": (i == 0)} for i in range(5)]
    kept = snapshots._rotate(index, max_unpinned=2)
    # p0 is the oldest and would rotate first — the pin holds it.
    assert [e["path"] for e in kept] == ["p0", "p3", "p4"]


def test_pinning_raises_the_total_rather_than_evicting_others():
    # A pin must not cost the user another snapshot: the cap bounds
    # automatic history only.
    index = [{"path": f"p{i}", "archive_id": None, "pinned": (i < 3)} for i in range(6)]
    kept = snapshots._rotate(index, max_unpinned=3)
    assert len(kept) == 6


def test_all_pinned_index_still_accepts_new_saves():
    # 10 pins must not mean the pre-apply snapshot the current run just
    # wrote is rotated out from under it.
    index = [{"path": f"p{i}", "archive_id": None, "pinned": True} for i in range(10)]
    index.append({"path": "new", "archive_id": None, "pinned": False})
    kept = snapshots._rotate(index, max_unpinned=10)
    assert kept[-1]["path"] == "new"


def test_pinned_snapshot_survives_rotation_end_to_end(fake_clock):
    first = snapshots.save(ROUTER, "oldest", label="pre-apply")
    assert snapshots.set_pinned(ROUTER, first, True) is True
    for i in range(12):
        snapshots.save(ROUTER, f"export {i}", label=f"s{i:02d}")

    listed = snapshots.list_snapshots(ROUTER)
    assert first in listed
    assert len(listed) == 11   # 10 rotating + 1 pinned


def test_set_pinned_reports_no_change_when_already_in_that_state(fake_clock):
    p = snapshots.save(ROUTER, "x", label="manual")
    assert snapshots.set_pinned(ROUTER, p, True) is True
    assert snapshots.set_pinned(ROUTER, p, True) is False
    assert snapshots.set_pinned(ROUTER, p, False) is True


def test_set_pinned_on_unknown_path_returns_false(fake_clock, tmp_path):
    snapshots.save(ROUTER, "x", label="manual")
    assert snapshots.set_pinned(ROUTER, tmp_path / "nope.rsc", True) is False


def test_list_metadata_exposes_pinned_state(fake_clock):
    p = snapshots.save(ROUTER, "x", label="manual")
    assert snapshots.list_metadata(ROUTER)[0].pinned is False
    snapshots.set_pinned(ROUTER, p, True)
    assert snapshots.list_metadata(ROUTER)[0].pinned is True


def test_legacy_index_entries_read_as_unpinned(snapdir):
    index_path = snapdir / f"{ROUTER.replace('.', '-')}-index.json"
    target = snapdir / "old.rsc"
    target.write_text("x")
    index_path.write_text(json.dumps([str(target)]))
    assert snapshots.list_metadata(ROUTER)[0].pinned is False


def test_rotation_status_reports_headroom(fake_clock):
    for i in range(3):
        snapshots.save(ROUTER, f"e{i}", label=f"s{i}")
    st = snapshots.rotation_status(ROUTER)
    assert (st.total, st.unpinned_count, st.pinned_count) == (3, 3, 0)
    assert st.at_limit is False
    assert st.next_to_rotate is None          # nothing is about to be lost
    assert st.oldest.label == "s0"


def test_rotation_status_names_the_next_casualty(fake_clock):
    paths = [snapshots.save(ROUTER, f"e{i}", label=f"s{i:02d}") for i in range(10)]
    st = snapshots.rotation_status(ROUTER)
    assert st.at_limit is True
    assert st.next_to_rotate.path == paths[0]


def test_rotation_status_skips_pinned_when_naming_the_next_casualty(fake_clock):
    paths = [snapshots.save(ROUTER, f"e{i}", label=f"s{i:02d}") for i in range(10)]
    snapshots.set_pinned(ROUTER, paths[0], True)
    st = snapshots.rotation_status(ROUTER)
    assert st.pinned_count == 1
    assert st.unpinned_count == 9
    assert st.at_limit is False               # the pin freed a rotating slot
    assert st.next_to_rotate is None


def test_rotation_status_empty_router():
    st = snapshots.rotation_status("10.9.9.9")
    assert st.total == 0 and st.oldest is None and st.next_to_rotate is None
