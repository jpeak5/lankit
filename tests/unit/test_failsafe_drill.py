"""
Unit tests for the test-failsafe drill (G1.9) and the shared failsafe
on-event builder it rehearses.

The command itself needs a live router and a real ~30s wait, so what is
covered here is everything that decides *whether the drill proves
anything*: that the on-event script it installs is production's, and that
its sentinel is a resource the production remove-pass would genuinely
delete.
"""
import inspect

from lankit.cli.commands._revert_shared import (
    FAILSAFE_NAME,
    FIRED_MARKER_NAME,
    REVERT_BUNDLE_NAME,
    build_failsafe_on_event,
)
from lankit.cli.commands import test_failsafe as drill
from lankit.core import revert


# ─── build_failsafe_on_event (design §6) ─────────────────────────────────────

def test_on_event_self_deletes_before_importing():
    script = build_failsafe_on_event()
    # One-shot property: if the import came first, a delayed cancel or a
    # session death at the wrong moment could let it fire twice.
    assert script.index("scheduler remove") < script.index("/import")


def test_on_event_leaves_a_durable_marker_on_both_branches():
    script = build_failsafe_on_event()
    assert "lankit:failsafe-fired:ok" in script
    assert "lankit:failsafe-fired:error" in script
    # RouterOS's /log buffer is too short-lived to be the only signal a
    # returning user sees (§7.2 amendment).
    assert script.count(f'name="{FIRED_MARKER_NAME}"') == 2


def test_on_event_defaults_to_the_production_bundle():
    assert f"/import file={REVERT_BUNDLE_NAME}" in build_failsafe_on_event()
    assert f'name="{FAILSAFE_NAME}"' in build_failsafe_on_event()


def test_only_the_import_filename_differs_between_modes():
    # The whole value of the drill rests on this: a rehearsal that runs a
    # different script than production proves less than it appears to.
    production = build_failsafe_on_event(REVERT_BUNDLE_NAME)
    rehearsal = build_failsafe_on_event(drill.DRILL_SCRIPT_NAME)
    assert production.replace(REVERT_BUNDLE_NAME, drill.DRILL_SCRIPT_NAME) == rehearsal


def test_apply_and_the_drill_share_one_builder():
    apply_src = inspect.getsource(
        __import__("lankit.cli.commands.apply", fromlist=["apply"]).apply.callback
    )
    drill_src = inspect.getsource(drill.test_failsafe.callback)
    assert "_build_failsafe_on_event()" in apply_src
    assert "build_failsafe_on_event(import_file)" in drill_src


# ─── The sentinel and the drill payload ──────────────────────────────────────

def test_sentinel_is_a_real_lankit_tagged_resource():
    # Not a decorative name: --full requires the production remove-pass to
    # delete it *because it is tagged*, like any other lankit resource.
    assert drill.SENTINEL_TAG.startswith("lankit:")


def test_production_remove_pass_would_delete_the_sentinel():
    filter_table = next(t for t in revert.TAG_TABLES if t.id == "filter")
    assert filter_table.removable
    # The remove-pass matches ^lankit: across /ip firewall filter, which is
    # exactly where the sentinel lives.
    assert 'comment~"^lankit:"' in filter_table.remove_script
    assert filter_table.path == "/ip firewall filter"
    assert drill.SENTINEL_TAG.startswith("lankit:")


def test_default_drill_payload_touches_only_the_sentinel():
    body = [ln for ln in drill.DRILL_SCRIPT.splitlines()
            if ln.strip() and not ln.startswith("#")]
    # Zero churn is the whole promise of the default mode: exactly one
    # command, and it names the sentinel.
    assert len(body) == 1
    assert drill.SENTINEL_TAG in body[0]
    for touched in ("/interface", "/ip dhcp", "/ip address", "/queue", "/ip dns"):
        assert touched not in drill.DRILL_SCRIPT


def test_drill_payload_is_not_named_like_the_revert_bundle():
    assert drill.DRILL_SCRIPT_NAME != REVERT_BUNDLE_NAME


# ─── Evidence readers ────────────────────────────────────────────────────────

class _FakeConn:
    def __init__(self, out=""):
        self.out = out

    def run_tolerant(self, cmd, timeout=30):
        return self.out, ""


def test_fired_marker_state_reads_ok():
    out = f'name="{FIRED_MARKER_NAME}" comment="lankit:failsafe-fired:ok"'
    assert drill._fired_marker_state(_FakeConn(out)) == "ok"


def test_fired_marker_state_reads_error():
    out = f'name="{FIRED_MARKER_NAME}" comment="lankit:failsafe-fired:error"'
    assert drill._fired_marker_state(_FakeConn(out)) == "error"


def test_fired_marker_absent_is_none():
    assert drill._fired_marker_state(_FakeConn("")) is None


def test_log_markers_parsed_independently():
    assert drill._check_log_markers(_FakeConn(
        "lankit-failsafe: fired - importing x\nlankit-failsafe: revert import finished"
    )) == (True, True)
    # A truncated buffer must be distinguishable from a clean run.
    assert drill._check_log_markers(_FakeConn("lankit-failsafe: fired - importing x")) == (True, False)
    assert drill._check_log_markers(_FakeConn("")) == (False, False)
