#!/opt/lankit-portal/venv/bin/python
import re
import subprocess
import sys

sys.path.insert(0, "/opt/lankit-portal")
import db

TARGETS = ["1.1.1.1", "8.8.8.8"]


def measure(target: str) -> float | None:
    try:
        out = subprocess.check_output(
            ["ping", "-c", "3", "-q", target], timeout=15, text=True, stderr=subprocess.DEVNULL
        )
        m = re.search(r"[\d.]+/([\d.]+)/[\d.]+", out)
        return float(m.group(1)) if m else None
    except Exception:
        return None


for target in TARGETS:
    rtt = measure(target)
    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO latency_log (target, rtt_ms) VALUES (?, ?)", (target, rtt)
        )

with db.get_db() as conn:
    conn.execute("DELETE FROM latency_log WHERE measured_at < datetime('now', '-24 hours')")
