"""
Unit tests for the pure helper functions extracted from lankit apply (G1.3):
_classify_import_error and _archive_update_plan. Both are pure — no router
I/O, no filesystem — so they're testable without a live connection, unlike
the surrounding CLI command itself (which needs a real RouterConnection and
is exercised by the G1.13 hardware-validation runbook instead).
"""
from dataclasses import dataclass

import pytest

from lankit.cli.commands.apply import _archive_update_plan, _classify_import_error


# ─── _classify_import_error ──────────────────────────────────────────────────

@pytest.mark.parametrize("err", ["", None])
def test_classify_import_error_empty_is_not_an_error(err):
    assert _classify_import_error(err) is False


@pytest.mark.parametrize("err", [
    "loading file 03-firewall.rsc",
    "script file loaded successfully",
    "warning: something informational",
])
def test_classify_import_error_info_patterns_are_not_errors(err):
    assert _classify_import_error(err) is False


@pytest.mark.parametrize("err", [
    "syntax error at line 3",
    "bad command name X",
    "no such item",
    "input does not match any value of interface",
    "failure: duplicate entry",
])
def test_classify_import_error_error_patterns_are_errors(err):
    assert _classify_import_error(err) is True


def test_classify_import_error_info_pattern_wins_when_both_present():
    # A RouterOS line containing both an info word ("warning") and an error
    # word should not be treated as fatal — matches the pre-G1.3 heuristic
    # this was extracted from verbatim.
    assert _classify_import_error("warning: expected behavior, ignore") is False


# ─── _archive_update_plan ────────────────────────────────────────────────────

@dataclass
class _FakeManifest:
    segments_applied: list
    globals_applied: bool


@dataclass
class _FakeArchive:
    manifest: _FakeManifest


def test_script_mode_never_updates_archive():
    assert _archive_update_plan(None, "03-firewall.rsc", None, ["a", "b"]) is None
    prior = _FakeArchive(_FakeManifest(["a"], True))
    assert _archive_update_plan("a", "03-firewall.rsc", prior, ["a", "b"]) is None


def test_full_apply_covers_all_segments_and_includes_globals():
    plan = _archive_update_plan(None, None, None, {"iot", "trusted"})
    assert plan == (["iot", "trusted"], True)


def test_full_apply_supersedes_prior_partial_archive():
    prior = _FakeArchive(_FakeManifest(["iot"], False))
    plan = _archive_update_plan(None, None, prior, ["trusted", "iot", "servers"])
    assert plan == (["iot", "servers", "trusted"], True)


def test_segment_apply_with_no_prior_archive_starts_fresh_no_globals():
    plan = _archive_update_plan("iot", None, None, ["trusted", "iot"])
    assert plan == (["iot"], False)


def test_segment_apply_unions_with_prior_archive_segments():
    prior = _FakeArchive(_FakeManifest(["trusted"], False))
    plan = _archive_update_plan("iot", None, prior, ["trusted", "iot", "servers"])
    assert plan == (["iot", "trusted"], False)


def test_segment_apply_preserves_prior_globals_applied_state():
    # A prior FULL apply set globals_applied=True; a later --segment apply
    # must not silently downgrade that back to False.
    prior = _FakeArchive(_FakeManifest(["trusted", "iot", "servers"], True))
    plan = _archive_update_plan("guest", None, prior, ["trusted", "iot", "servers", "guest"])
    assert plan == (["guest", "iot", "servers", "trusted"], True)


def test_segment_apply_reapplying_same_segment_does_not_duplicate():
    prior = _FakeArchive(_FakeManifest(["iot"], False))
    plan = _archive_update_plan("iot", None, prior, ["trusted", "iot"])
    assert plan == (["iot"], False)


# ─── G3.7 wiring: revert bundle re-enables the defconf fasttrack rule ────────

def test_apply_builds_bundle_with_fasttrack_reenable():
    # The apply command itself needs a live RouterConnection, so this is a
    # source-level guard: 03-firewall.rsc disables the defconf fasttrack
    # rule in place (untagged, tag-scoped-revert.md §5.3), so apply MUST
    # request the re-enable when it builds the revert bundle.
    import inspect
    import lankit.cli.commands.apply as apply_mod

    src = inspect.getsource(apply_mod)
    assert "fasttrack_reenable=True" in src


# ─── G1.6: post-apply probe (issue #1) ───────────────────────────────────────

from lankit.cli.commands.apply import (  # noqa: E402
    _DNS_PROBE_NAME,
    _probe_plan,
    _probe_verdict,
    _resolve_check,
)


def test_probe_plan_full_apply_checks_every_gateway(cfg):
    gateways, dns_ip = _probe_plan(cfg, None)
    assert [name for name, _ in gateways] == list(cfg.segments.keys())
    assert all(gw.endswith(".1") for _, gw in gateways)


def test_probe_plan_segment_apply_checks_only_that_gateway(cfg):
    name = next(iter(cfg.segments))
    gateways, _ = _probe_plan(cfg, name)
    # An incremental build-out legitimately has other segments not up yet —
    # pinging them would fail for reasons this apply is not answerable for.
    assert gateways == [(name, cfg.segments[name].gateway)]


def test_probe_plan_uses_managed_resolver_ip(cfg):
    _, dns_ip = _probe_plan(cfg, None)
    assert dns_ip == cfg.hosts["dns_server"].ip


def test_probe_plan_skips_dns_when_no_managed_resolver(no_pihole_yml):
    from lankit.core.config import load
    # dns_ip points at a public resolver this apply cannot have broken.
    _, dns_ip = _probe_plan(load(no_pihole_yml), None)
    assert dns_ip is None


def test_probe_verdict_all_green():
    ok, summary, failures = _probe_verdict(
        [("trusted", "10.10.0.1", True, "3/3 packets"),
         ("iot", "10.30.0.1", True, "3/3 packets")],
        (True, "10.20.0.2", "104.21.0.1"),
    )
    assert ok is True
    assert failures == []
    assert "2/2 gateways reachable" in summary
    assert "answered" in summary


def test_probe_verdict_unreachable_gateway_fails():
    ok, summary, failures = _probe_verdict(
        [("trusted", "10.10.0.1", True, "3/3 packets"),
         ("iot", "10.30.0.1", False, "0/3 packets")],
        None,
    )
    assert ok is False
    assert "1/2 gateways reachable" in summary
    assert len(failures) == 1
    assert "iot" in failures[0] and "10.30.0.1" in failures[0]


def test_probe_verdict_dns_failure_alone_fails():
    # force_dns redirects and the DHCP-advertised resolver are router config
    # this apply just wrote, so a dead resolver is a failed apply until the
    # user says otherwise — not a silent pass.
    ok, _, failures = _probe_verdict(
        [("trusted", "10.10.0.1", True, "3/3 packets")],
        (False, "10.20.0.2", "no answer"),
    )
    assert ok is False
    assert len(failures) == 1
    assert "10.20.0.2" in failures[0]


def test_probe_verdict_no_checks_at_all_is_ok():
    ok, summary, failures = _probe_verdict([], None)
    assert ok is True
    assert failures == []


class _FakeConn:
    def __init__(self, out="", err=""):
        self.out, self.err, self.commands = out, err, []

    def run_tolerant(self, cmd, timeout=30):
        self.commands.append(cmd)
        return self.out, self.err


def test_resolve_check_accepts_an_answer():
    conn = _FakeConn(out="159.148.147.196\n")
    ok, detail = _resolve_check(conn, "10.20.0.2")
    assert ok is True
    assert detail == "159.148.147.196"
    assert f"server=10.20.0.2" in conn.commands[0]
    assert _DNS_PROBE_NAME in conn.commands[0]


def test_resolve_check_reports_resolver_error():
    conn = _FakeConn(out="", err="failure: dns server failure\n")
    ok, detail = _resolve_check(conn, "10.20.0.2")
    assert ok is False
    assert detail == "failure: dns server failure"


def test_resolve_check_treats_silence_as_failure():
    # The FTL-hang case (2026-07-20): host up, ICMP fine, lookups hang.
    # A ping-only check would have called this healthy.
    conn = _FakeConn(out="", err="")
    ok, detail = _resolve_check(conn, "10.20.0.2")
    assert ok is False
    assert detail == "no answer"


# ─── G1.7: import residue, apply.log, --strict (issue #32) ───────────────────

from lankit.cli.commands.apply import _format_apply_log, _write_apply_log  # noqa: E402


def test_format_apply_log_records_every_script():
    text = _format_apply_log(
        "192.168.88.1", "all segments",
        [("00-bridge.rsc", "ok\n", ""), ("03-firewall.rsc", "", "failure: duplicate entry\n")],
        "2026-07-27T21:00:00Z",
    )
    assert "192.168.88.1" in text
    assert "all segments" in text
    assert "2026-07-27T21:00:00Z" in text
    assert "00-bridge.rsc" in text and "03-firewall.rsc" in text
    assert "failure: duplicate entry" in text


def test_format_apply_log_marks_empty_streams_explicitly():
    # "(empty)" rather than a blank gap: a reader diagnosing a half-applied
    # router must be able to tell "nothing was written" from "the log is
    # truncated".
    text = _format_apply_log("10.0.0.1", "segment iot", [("01-vlans.rsc", "", "")],
                             "2026-07-27T21:00:00Z")
    assert text.count("(empty)") == 2


def test_write_apply_log_appends_across_runs(tmp_path):
    d = tmp_path / "generated"
    _write_apply_log(d, "192.168.88.1", "segment admin", [("01-vlans.rsc", "a", "")])
    path = _write_apply_log(d, "192.168.88.1", "segment iot", [("01-vlans.rsc", "b", "")])
    text = path.read_text()
    # An incremental build-out is several applies; the sequence is the
    # diagnosis, so a later run must not erase an earlier one.
    assert "segment admin" in text and "segment iot" in text
    assert text.count("lankit apply —") == 2


def test_write_apply_log_survives_an_unwritable_target(tmp_path):
    blocker = tmp_path / "generated"
    blocker.write_text("not a directory")
    # Logging must never be able to break an apply that otherwise worked.
    assert _write_apply_log(blocker, "192.168.88.1", "all segments", []) is None


def test_strict_flag_is_wired_to_the_import_loop():
    # The command body needs a live RouterConnection, so this is a
    # source-level guard that --strict actually reaches the fatal path
    # rather than only existing as a flag.
    import inspect
    import lankit.cli.commands.apply as apply_mod

    src = inspect.getsource(apply_mod.apply.callback)
    assert "elif strict:" in src
    assert "strict_tripped = True" in src


def test_residue_makes_the_keep_prompt_default_to_no():
    import inspect
    import lankit.cli.commands.apply as apply_mod

    src = inspect.getsource(apply_mod.apply.callback)
    assert '_print_residue_summary(residue, log_path, console)' in src
    assert '**({"default": False} if residue else {})' in src


# ─── G3.3: global-rule pre-flight for --segment mode (issue #20) ─────────────

from lankit.cli.commands.apply import _globals_present, _segment_next_steps  # noqa: E402


def test_globals_present_detects_the_default_deny_rule():
    out = ';;; lankit:fw:all:default-deny\n    chain=forward action=drop\n'
    assert _globals_present(_FakeConn(out=out)) is True


def test_globals_present_accepts_pre_rename_kit_tags():
    # A router provisioned before the kit: → lankit: rename is configured,
    # not bare — warning it would be wrong.
    assert _globals_present(_FakeConn(out=';;; kit:fw:all:default-deny\n')) is True


def test_globals_present_false_on_a_bare_firewall_table():
    out = ';;; defconf: accept established,related\n    chain=input\n'
    assert _globals_present(_FakeConn(out=out)) is False


def test_globals_present_none_when_the_table_cannot_be_read():
    # Undetermined must not read as "absent" — that would fire the warning
    # on a fully-configured router.
    assert _globals_present(_FakeConn(err="bad command name print")) is None


def test_segment_next_steps_lists_uncovered_segments():
    assert _segment_next_steps(
        ["admin", "trusted", "servers", "iot"], ["admin", "trusted"]
    ) == ["servers", "iot"]


def test_segment_next_steps_empty_when_all_covered():
    assert _segment_next_steps(["admin", "iot"], ["iot", "admin"]) == []


def test_yes_flag_does_not_reach_the_keep_prompt():
    # --yes skips pre-flight confirmations only. If it ever gated the
    # keep/revert decision, an unattended apply could keep a broken config.
    import inspect
    import lankit.cli.commands.apply as apply_mod

    src = inspect.getsource(apply_mod.apply.callback)
    keep_call = src[src.index("keep = _ask_keep("):]
    assert "yes" not in keep_call[:keep_call.index(")")]
