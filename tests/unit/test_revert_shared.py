"""
Unit tests for lankit.cli.commands._revert_shared (G1.4) — the shared,
mockable parts of the tag-scoped rollback/restore flow: import-error
classification lives in test_apply.py (unchanged, via apply's aliases);
here we cover snapshot↔archive pairing resolution, the connect-time
failsafe-fired check, verified upload, import+verify, and the evidence
printer. The live command flows themselves (click prompts over a real
RouterConnection) are exercised by the G1.13 hardware-validation runbook,
matching G1.3's precedent.

Also covers the two G1.11 reviewer failure paths for archive retention:
an unpaired bootstrap archive surviving an index write, and paired-
snapshot rotation not deleting the current archive.
"""
import io

import pytest
from rich.console import Console

from lankit.cli.commands import _revert_shared as shared
from lankit.core import revert, snapshots
from lankit.core.revert import RevertVerdict

ROUTER = "192.168.88.1"


@pytest.fixture(autouse=True)
def isolated_dirs(tmp_path, monkeypatch):
    monkeypatch.setattr(snapshots, "_SNAPSHOTS_DIR", tmp_path / "snapshots")
    monkeypatch.setattr(revert, "_KNOWN_GOOD_DIR", tmp_path / "known-good")


@pytest.fixture
def console():
    return Console(file=io.StringIO(), width=200, no_color=True, highlight=False,
                   force_terminal=False, legacy_windows=False)


def _console_text(console) -> str:
    return console.file.getvalue()


def _save_archive(text="a\n", **overrides):
    kwargs = dict(
        segments_applied=["trusted"], globals_applied=True,
        network_yml_hash="h1", lan_interface="ether2",
    )
    kwargs.update(overrides)
    return revert.save_known_good(ROUTER, {"01-vlans.rsc": text}, **kwargs)


class FakeConn:
    """Scripted RouterConnection stand-in — never talks to anything."""

    def __init__(self, tolerant_responses=None, live_tags=frozenset(), verify_ok=True):
        self.commands = []
        self.uploads = []
        self._responses = tolerant_responses or {}
        self._live_tags = set(live_tags)
        self._verify_ok = verify_ok

    def run_tolerant(self, cmd, timeout=30):
        self.commands.append(cmd)
        for key, resp in self._responses.items():
            if key in cmd:
                return resp
        return ("", "")

    def upload(self, content, remote_path):
        self.uploads.append((remote_path, content))

    def verify_uploaded_file(self, remote_path, expected_size):
        return self._verify_ok

    def read_lankit_tags(self):
        return set(self._live_tags)


# ─── current_archive_id / resolve_paired_archive ────────────────────────────

def test_current_archive_id_none_when_no_archive():
    assert shared.current_archive_id(ROUTER) is None


def test_current_archive_id_returns_latest():
    import time
    _save_archive("a\n")
    time.sleep(0.01)
    aid2 = _save_archive("b\n")
    assert shared.current_archive_id(ROUTER) == aid2


def test_resolve_paired_archive_paired():
    aid = _save_archive()
    path = snapshots.save(ROUTER, "# export\n", label="post-apply", archive_id=aid)
    status, archive = shared.resolve_paired_archive(ROUTER, path)
    assert status == "paired"
    assert archive.manifest.archive_id == aid


def test_resolve_paired_archive_unpaired_snapshot():
    path = snapshots.save(ROUTER, "# export\n", label="pre-apply")
    status, archive = shared.resolve_paired_archive(ROUTER, path)
    assert status == "unpaired"
    assert archive is None


def test_resolve_paired_archive_unknown_path_is_unpaired(tmp_path):
    # An arbitrary file path outside the index (restore accepts one).
    stray = tmp_path / "stray.rsc"
    stray.write_text("# export\n")
    status, archive = shared.resolve_paired_archive(ROUTER, stray)
    assert status == "unpaired"
    assert archive is None


def test_resolve_paired_archive_missing_archive_is_not_silently_downgraded():
    aid = _save_archive()
    path = snapshots.save(ROUTER, "# export\n", label="post-apply", archive_id=aid)
    revert.delete_known_good(ROUTER, aid)
    status, archive = shared.resolve_paired_archive(ROUTER, path)
    assert status == "archive-missing"
    assert archive is None


# ─── G1.11 reviewer failure paths: archive retention ────────────────────────

def test_bootstrap_archive_survives_unpaired_index_write():
    # Path 1: migration bootstrap calls save_known_good directly, no
    # snapshot pairs to it yet. A routine snapshots.save(archive_id=None)
    # (commit/rollback/restore pre-op saves before G1.4, or any index
    # write) must not sweep it.
    aid = _save_archive()
    snapshots.save(ROUTER, "# export\n", label="pre-apply")  # archive_id=None
    assert revert.list_known_good_ids(ROUTER) == [aid]


def test_rotation_of_only_paired_snapshot_does_not_delete_current_archive():
    # Path 2: ten unpaired saves rotate the single paired post-apply
    # snapshot out of the 10-cap index; the current archive must survive.
    aid = _save_archive()
    snapshots.save(ROUTER, "# export\n", label="post-apply", archive_id=aid)
    for i in range(10):
        snapshots.save(ROUTER, f"# export {i}\n", label="commit")
    paired_ids = {e["archive_id"] for e in snapshots._read_index(ROUTER) if e["archive_id"]}
    assert paired_ids == set()  # the paired snapshot really rotated out
    assert revert.list_known_good_ids(ROUTER) == [aid]


# ─── check_failsafe_fired_marker ────────────────────────────────────────────

def test_fired_marker_absent_prints_nothing(console):
    conn = FakeConn()
    shared.check_failsafe_fired_marker(conn, console)
    assert _console_text(console) == ""


def test_fired_marker_present_warns_and_can_continue(console, monkeypatch):
    monkeypatch.setattr("rich.prompt.Confirm.ask", lambda *a, **k: True)
    conn = FakeConn(tolerant_responses={
        "/system scheduler print detail": (
            'name="lankit-failsafe-fired" comment="lankit:failsafe-fired:error"', ""),
        "/log print": ("lankit-failsafe: revert import FAILED - run lankit probe", ""),
    })
    shared.check_failsafe_fired_marker(conn, console)
    text = _console_text(console)
    assert "failsafe fired previously" in text
    assert "error" in text
    assert "lankit probe" in text


def test_fired_marker_present_decline_exits(console, monkeypatch):
    monkeypatch.setattr("rich.prompt.Confirm.ask", lambda *a, **k: False)
    conn = FakeConn(tolerant_responses={
        "/system scheduler print detail": (
            'name="lankit-failsafe-fired" comment="lankit:failsafe-fired:ok"', ""),
    })
    with pytest.raises(SystemExit):
        shared.check_failsafe_fired_marker(conn, console)


# ─── upload_revert_bundle / import_revert_bundle_and_verify ─────────────────

def test_upload_revert_bundle_uploads_canonical_name_and_verifies(console):
    conn = FakeConn(verify_ok=True)
    assert shared.upload_revert_bundle(conn, "# bundle\n", console) is True
    assert conn.uploads == [("lankit-revert.rsc", "# bundle\n")]


def test_upload_revert_bundle_reports_unverified(console):
    conn = FakeConn(verify_ok=False)
    assert shared.upload_revert_bundle(conn, "# bundle\n", console) is False


def test_import_and_verify_green_path(console):
    expected = {"lankit:vlan:trusted:interface"}
    conn = FakeConn(live_tags=expected)
    import_clean, verdict = shared.import_revert_bundle_and_verify(conn, expected, console)
    assert import_clean is True
    assert verdict.ok
    assert any(cmd == "/import file=lankit-revert.rsc" for cmd in conn.commands)


def test_import_and_verify_flags_hard_stderr(console):
    conn = FakeConn(
        tolerant_responses={"/import": ("", "syntax error at line 12")},
        live_tags=set(),
    )
    import_clean, verdict = shared.import_revert_bundle_and_verify(conn, set(), console)
    assert import_clean is False
    assert "syntax error" in _console_text(console)


def test_import_and_verify_reports_convergence_shortfall(console):
    conn = FakeConn(live_tags={"lankit:fw:iot:isolation"})
    import_clean, verdict = shared.import_revert_bundle_and_verify(
        conn, {"lankit:vlan:trusted:interface"}, console)
    assert import_clean is True
    assert not verdict.ok
    assert verdict.missing == frozenset({"lankit:vlan:trusted:interface"})
    assert verdict.unexpected == frozenset({"lankit:fw:iot:isolation"})


# ─── print_verdict_evidence ─────────────────────────────────────────────────

def test_evidence_lists_missing_and_unexpected(console):
    verdict = RevertVerdict(
        missing=frozenset({"lankit:vlan:trusted:interface"}),
        unexpected=frozenset({"lankit:fw:iot:isolation"}),
    )
    shared.print_verdict_evidence(verdict, console)
    text = _console_text(console)
    assert "Missing (1)" in text
    assert "lankit:vlan:trusted:interface" in text
    assert "Unexpected (1)" in text
    assert "lankit:fw:iot:isolation" in text


def test_evidence_truncates_long_lists(console):
    verdict = RevertVerdict(
        missing=frozenset(f"lankit:dns:seg{i}:gateway" for i in range(15)),
        unexpected=frozenset(),
    )
    shared.print_verdict_evidence(verdict, console)
    text = _console_text(console)
    assert "Missing (15)" in text
    assert "and 5 more" in text


def test_evidence_notes_legacy_kit_tags(console):
    verdict = RevertVerdict(
        missing=frozenset(),
        unexpected=frozenset({"kit:fw:iot:isolation"}),
    )
    shared.print_verdict_evidence(verdict, console)
    text = _console_text(console)
    assert "pre-rename legacy tags" in text
    assert "manual" in text


def test_evidence_silent_on_ok_verdict(console):
    shared.print_verdict_evidence(RevertVerdict(frozenset(), frozenset()), console)
    assert _console_text(console) == ""


# ─── ENH-008 (#16) context display: age, label meaning, size, pairing ────────

def _snapshot(tmp_path, label="pre-apply", age_days=0, size_kb=24, archive_id=None):
    from datetime import datetime, timedelta, timezone
    path = tmp_path / f"192-168-88-1-20260706T120000Z-{label}.rsc"
    path.write_text("x" * (size_kb * 1024 + 10))
    return snapshots.Snapshot(
        path=path, router_ip=ROUTER,
        timestamp=datetime.now(timezone.utc) - timedelta(days=age_days),
        label=label, archive_id=archive_id,
    )


@pytest.mark.parametrize("delta,expected", [
    (dict(seconds=5), "just now"),
    (dict(minutes=20), "20 minutes ago"),
    (dict(hours=5), "5 hours ago"),
    (dict(days=3), "3 days ago"),
    (dict(days=150), "5 months ago"),
    (dict(days=400), "1 year ago"),
    (dict(days=800), "2 years ago"),
])
def test_format_age_is_human_readable(delta, expected):
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
    assert shared.format_age(now - timedelta(**delta), now=now) == expected


def test_format_age_never_reports_a_negative_age():
    from datetime import datetime, timedelta, timezone
    now = datetime(2026, 7, 27, 12, 0, tzinfo=timezone.utc)
    assert shared.format_age(now + timedelta(hours=2), now=now) == "just now"


@pytest.mark.parametrize("label,fragment", [
    ("pre-apply", "just before a lankit apply"),
    ("pre-apply-iot", "'iot' segment apply"),
    ("post-apply", "a completed lankit apply produced"),
    ("pre-rollback", "just before a lankit rollback"),
    ("pre-restore", "just before a lankit restore"),
    ("commit", "lankit commit"),
    ("whatever", "by hand"),
])
def test_describe_label_explains_what_the_label_means(label, fragment):
    assert fragment in shared.describe_label(label)


def test_context_block_shows_age_label_size_and_pairing(console, tmp_path):
    aid = _save_archive()
    archive = revert.load_known_good(ROUTER, aid)
    snap = _snapshot(tmp_path, age_days=150, size_kb=24, archive_id=aid)

    shared.print_snapshot_context(console, snap, "paired", archive,
                                  heading="Will roll back to snapshot:")
    text = _console_text(console)
    assert snap.path.name in text
    assert "5 months ago" in text
    assert "2026-" in text          # absolute timestamp alongside the age
    assert "pre-apply" in text
    assert "just before a lankit apply" in text
    assert "24 KB" in text
    assert aid in text
    assert "segments: trusted" in text


def test_context_block_flags_an_unpaired_snapshot(console, tmp_path):
    snap = _snapshot(tmp_path, label="pre-apply")
    shared.print_snapshot_context(console, snap, "unpaired", None,
                                  heading="Will roll back to snapshot:")
    text = _console_text(console)
    assert "unpaired (legacy)" in text
    assert "no known-good archive" in text


def test_context_block_flags_a_missing_archive(console, tmp_path):
    snap = _snapshot(tmp_path)
    shared.print_snapshot_context(console, snap, "archive-missing", None,
                                  heading="Will roll back to snapshot:")
    assert "missing from disk" in _console_text(console)


def test_context_block_prints_extra_walk_lines(console, tmp_path):
    snap = _snapshot(tmp_path)
    shared.print_snapshot_context(console, snap, "unpaired", None,
                                  heading="Will roll back to snapshot:",
                                  extra_lines=["Walk:     second rollback since the last apply"])
    assert "second rollback since the last apply" in _console_text(console)
