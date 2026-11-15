"""
lankit.core.untagged
~~~~~~~~~~~~~~~~~~~~

Objects on the router that lankit doesn't own. Pure functions, no router I/O.

lankit owns what carries a ``lankit:`` tag, and builds on MikroTik's default
configuration (``defconf``). Anything else, in a table lankit writes to, is
config someone made by hand: lankit's remove-passes never touch it and its
revert never restores it. Two reasons to surface it:

* **Drift.** The aim is that lankit describes the whole router. An untagged
  object is a gap in that description (``lankit audit`` lists them).
* **Collisions (#70).** An untagged object with the same identity as one
  lankit is about to ``add`` (an existing ``vlan-trusted`` interface, a pool
  of the same name) survives the remove-pass, so the ``add`` fails with
  "already have such ...". ``lankit apply`` checks for these before the
  failsafe is armed, when refusing costs nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Iterable

from lankit.core.revert import TAG_TABLES, _fields, _split_records

_TABLE_PATHS = {t.id: t.path for t in TAG_TABLES}

# Defconf objects that RouterOS creates without a "defconf" comment.
_UNCOMMENTED_DEFCONF: dict[str, dict[str, str]] = {
    "pool": {"name": "default-dhcp"},
    "dhcp-server": {"name": "defconf"},
}

# The fields that make two objects "the same" to RouterOS, per table: an add
# whose identity matches a live object fails, or would duplicate it. Tables
# without a stable identity (firewall rules, static DNS) are left out; they
# are still reported as untagged, just never as collisions.
_IDENTITY: dict[str, tuple[str, ...]] = {
    "vlan":          ("name",),
    "pool":          ("name",),
    "dhcp-server":   ("name",),
    "dhcp-network":  ("address",),
    "dhcp-lease":    ("mac-address",),
    "address":       ("address",),
    "address-list":  ("list", "address"),
    "list-member":   ("list", "interface"),
    "bridge-port":   ("interface",),
    "bridge-vlan":   ("vlan-ids",),
    "wifi":          ("name",),
    "wifi-security": ("name",),
    "queue-tree":    ("name",),
}

_FLAGS_RE = re.compile(r"^\s{0,6}\d+((?:\s+[A-Z]+)*)(?=\s|$)")


@dataclass(frozen=True)
class Untagged:
    table: str          # TAG_TABLES id
    path: str           # RouterOS menu, e.g. "/interface vlan"
    comment: str        # "" when the object has none
    fields: tuple[tuple[str, str], ...]

    @property
    def label(self) -> str:
        """A short human description: the identity fields, else the first few."""
        d = dict(self.fields)
        keys = _IDENTITY.get(self.table) or tuple(k for k, _ in self.fields[:3])
        parts = [f"{k}={d[k]}" for k in keys if k in d]
        return " ".join(parts) or "(no fields)"

    def identity(self) -> tuple[str, ...] | None:
        keys = _IDENTITY.get(self.table)
        if not keys:
            return None
        d = dict(self.fields)
        if any(k not in d for k in keys):
            return None
        return tuple(_norm(d[k]) for k in keys)


def _norm(value: str) -> str:
    return value.strip('"').lower()


def _record_flags(output: str) -> list[str]:
    """Flags for each record in print-detail output, in record order.

    _split_records drops the flag column (D = dynamic, X = disabled, ...);
    this walks the same index lines to recover it. Kept separate so the
    shared parser's output (relied on by revert verification) is unchanged.
    """
    flags: list[str] = []
    for line in output.splitlines():
        m = _FLAGS_RE.match(line.split(";;;")[0])
        if m:
            flags.append("".join(m.group(1).split()))
    return flags


def _is_dynamic(rec: dict[str, str], flags: str) -> bool:
    return "D" in flags or rec.get("dynamic") == "yes"


def _is_owned_or_default(table: str, rec: dict[str, str]) -> bool:
    comment = rec.get("comment", "")
    if comment.startswith("lankit:") or comment.startswith("defconf"):
        return True
    expected = _UNCOMMENTED_DEFCONF.get(table)
    if expected and all(_norm(rec.get(k, "")) == v for k, v in expected.items()):
        return True
    if table == "wifi" and "master-interface" not in rec:
        return True     # a radio, not a virtual AP; lankit `set`s these
    if table == "scheduler" and rec.get("name", "").startswith("lankit-"):
        return True     # the failsafe, matched by name, never by comment
    return False


def find_untagged(raw_outputs: dict[str, str]) -> list[Untagged]:
    """Every static, non-defconf object without a lankit: tag.

    raw_outputs: {TAG_TABLES id: print-detail stdout}, as gathered by
    RouterConnection.read_tag_tables (tables whose print errored are absent).
    """
    found: list[Untagged] = []
    for table in TAG_TABLES:
        text = raw_outputs.get(table.id)
        if not text:
            continue
        records = _split_records(text)
        flags = _record_flags(text)
        if len(flags) != len(records):
            flags = [""] * len(records)     # unparseable flag column: fall back
        for rec, fl in zip(records, flags):
            if _is_dynamic(rec, fl) or _is_owned_or_default(table.id, rec):
                continue
            fields = tuple((k, v) for k, v in rec.items() if k != "comment")
            found.append(Untagged(table.id, table.path, rec.get("comment", ""), fields))
    return found


# ─── What lankit is about to add ────────────────────────────────────────────

_PATH_TO_TABLE = {t.path: t.id for t in TAG_TABLES}


def scan_adds(text: str) -> list[tuple[str, dict[str, str]]]:
    """(table id, fields) for every `add` in a generated script.

    Reads lankit's own rendered .rsc text, whose shape we control: a menu
    line (`/ip pool`), then `add ...` commands with `\\` continuations.
    """
    adds: list[tuple[str, dict[str, str]]] = []
    menu = None
    pending: list[str] | None = None
    for raw in text.splitlines():
        line = raw.strip()
        if pending is not None:
            pending.append(line.rstrip("\\").strip())
            if not line.endswith("\\"):
                if menu in _PATH_TO_TABLE:
                    adds.append((_PATH_TO_TABLE[menu], _fields(" ".join(pending))))
                pending = None
            continue
        if not line or line.startswith("#") or line.startswith(":"):
            continue
        if line.startswith("/") and "=" not in line and "[" not in line:
            menu = line
        elif line.startswith("add ") or line == "add":
            body = line[4:].rstrip("\\").strip()
            if line.endswith("\\"):
                pending = [body]
            elif menu in _PATH_TO_TABLE:
                adds.append((_PATH_TO_TABLE[menu], _fields(body)))
    return adds


def find_collisions(untagged: Iterable[Untagged], script_texts: Iterable[str]) -> list[Untagged]:
    """Untagged objects that an `add` in script_texts would collide with."""
    planned: set[tuple[str, tuple[str, ...]]] = set()
    for text in script_texts:
        for table, fields in scan_adds(text):
            keys = _IDENTITY.get(table)
            if keys and all(k in fields for k in keys):
                planned.add((table, tuple(_norm(fields[k]) for k in keys)))
    return [u for u in untagged
            if (ident := u.identity()) is not None and (u.table, ident) in planned]
