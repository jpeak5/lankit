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


@dataclass
class Snapshot:
    path: Path
    router_ip: str
    timestamp: datetime
    label: str

    @property
    def size_kb(self) -> int:
        return self.path.stat().st_size // 1024


def save(router_ip: str, config_export: str, label: str = "apply") -> Path:
    """Save a config export snapshot and return its path."""
    _SNAPSHOTS_DIR.mkdir(parents=True, exist_ok=True)
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    safe_ip = router_ip.replace(".", "-")
    path = _SNAPSHOTS_DIR / f"{safe_ip}-{ts}-{label}.rsc"
    path.write_text(config_export)
    _update_index(router_ip, path)
    return path


def list_snapshots(router_ip: str) -> list[Path]:
    """Return list of snapshot paths for this router, oldest-first."""
    index = _read_index(router_ip)
    return [Path(p) for p in index if Path(p).exists()]


def list_metadata(router_ip: str) -> list[Snapshot]:
    """Return Snapshot objects with parsed metadata, oldest-first."""
    result = []
    for path in list_snapshots(router_ip):
        m = _TS_RE.search(path.name)
        if m:
            ts = datetime.strptime(m.group(1), "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
            label = m.group(2)
        else:
            ts = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            label = path.stem
        result.append(Snapshot(path=path, router_ip=router_ip, timestamp=ts, label=label))
    return result


def delete(router_ip: str, path: Path) -> None:
    """Delete a snapshot and remove it from the index."""
    path.unlink(missing_ok=True)
    index = _read_index(router_ip)
    index = [p for p in index if Path(p) != path]
    _index_path(router_ip).write_text(json.dumps(index, indent=2))


def latest(router_ip: str) -> Optional[Path]:
    """Return the most recent snapshot path, or None."""
    snaps = list_snapshots(router_ip)
    return snaps[-1] if snaps else None


def previous(router_ip: str) -> Optional[Path]:
    """Return the snapshot before the latest (for rollback), or None."""
    snaps = list_snapshots(router_ip)
    if len(snaps) >= 2:
        return snaps[-2]
    return snaps[0] if snaps else None


def _read_index(router_ip: str) -> list[str]:
    index_path = _index_path(router_ip)
    if not index_path.exists():
        return []
    try:
        return json.loads(index_path.read_text())
    except (json.JSONDecodeError, OSError):
        return []


def _update_index(router_ip: str, path: Path) -> None:
    index = _read_index(router_ip)
    index.append(str(path))
    index = index[-_MAX_SNAPSHOTS:]  # keep last N
    _index_path(router_ip).write_text(json.dumps(index, indent=2))


def _index_path(router_ip: str) -> Path:
    safe_ip = router_ip.replace(".", "-")
    return _SNAPSHOTS_DIR / f"{safe_ip}-index.json"
