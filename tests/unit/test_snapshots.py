"""
Unit tests for lankit.core.snapshots — save, index, metadata parsing,
latest/previous semantics, rotation at the cap, and delete.

_SNAPSHOTS_DIR is monkeypatched to tmp_path so nothing touches
~/.lankit/snapshots/. Timestamps are made deterministic by patching
the module's datetime with a fake whose now() advances one minute
per call.
"""
from datetime import datetime, timedelta, timezone

import pytest

import lankit.core.snapshots as snapshots

ROUTER = "192.168.88.1"
SAFE = "192-168-88-1"


@pytest.fixture(autouse=True)
def snapdir(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshots, "_SNAPSHOTS_DIR", tmp_path)
    return tmp_path


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
