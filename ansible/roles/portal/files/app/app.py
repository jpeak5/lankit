import logging
import re
from datetime import datetime, timedelta, timezone

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, render_template, request

import config
import db
from pihole import PiholeClient

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)

# Initialised in startup()
pihole: PiholeClient = None
scheduler: BackgroundScheduler = None
_default_group_id: int = 0
_bypass_group_id: int | None = None

BYPASS_GROUP = "lankit-bypass"
RESERVED_NAMES = {"router", "dns", "apps", "me", "network", "register"}
HOSTNAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9\-]{0,29}$")
JOBS_DB = "sqlite:////var/lib/lankit-portal/jobs.db"


# ── Startup ───────────────────────────────────────────────────────────────────

def _resolve_groups():
    global _default_group_id, _bypass_group_id
    data = pihole.get_groups()
    groups = data.get("groups", data) if isinstance(data, dict) else data
    for g in groups:
        if g.get("id") == 0 or g.get("name") == "Default":
            _default_group_id = g["id"]
        if g.get("name") == BYPASS_GROUP:
            _bypass_group_id = g["id"]
    if _bypass_group_id is None:
        pihole.create_group(BYPASS_GROUP, "lankit managed — ad block bypass")
        # Pi-hole v6 create returns {"groups": [...]} — re-fetch to get canonical ID
        data = pihole.get_groups()
        for g in (data.get("groups", data) if isinstance(data, dict) else data):
            if g.get("name") == BYPASS_GROUP:
                _bypass_group_id = g["id"]
                break
        if _bypass_group_id is None:
            raise RuntimeError(f"Could not find or create bypass group {BYPASS_GROUP!r}")
        log.info("Created bypass group id=%d", _bypass_group_id)


def _recover_bypasses():
    """Revert any bypass that has no active scheduler job (survived a restart)."""
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT ip, mac FROM bypass_log WHERE ended_at IS NULL AND cancelled = 0"
        ).fetchall()
    for row in rows:
        job_id = row["mac"] or row["ip"]
        if not scheduler.get_job(job_id):
            log.info("Reverting orphaned bypass for %s", row["ip"])
            _do_revert(row["ip"], row["mac"])


def startup():
    global pihole, scheduler
    db.create_all()
    scheduler = BackgroundScheduler(
        jobstores={"default": SQLAlchemyJobStore(url=JOBS_DB)}
    )
    scheduler.start()
    try:
        pihole = PiholeClient(config.pihole_url, config.pihole_password)
        _resolve_groups()
        _recover_bypasses()
        log.info("Portal started — bypass group id=%d", _bypass_group_id)
    except Exception:
        log.exception(
            "Pi-hole unreachable at startup (%s) — portal will serve degraded "
            "pages until connectivity is restored",
            config.pihole_url,
        )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _ensure_pihole() -> bool:
    """Lazy-init Pi-hole client if startup failed. Returns True if ready."""
    global pihole
    if pihole is not None:
        return True
    try:
        pihole = PiholeClient(config.pihole_url, config.pihole_password)
        _resolve_groups()
        _recover_bypasses()
        log.info("Pi-hole connected (deferred) — bypass group id=%d", _bypass_group_id)
        return True
    except Exception:
        log.warning("Pi-hole still unreachable at %s", config.pihole_url)
        return False


def _client_ip() -> str:
    return request.headers.get("X-Real-IP") or request.remote_addr


def _bypass_remaining_seconds(job_id: str) -> int | None:
    job = scheduler.get_job(job_id)
    if not job:
        return None
    delta = job.next_run_time - datetime.now(timezone.utc)
    return max(0, int(delta.total_seconds()))


def _do_revert(ip: str, mac: str | None):
    """Re-enable blocking. Called by scheduler on expiry or by cancel route."""
    try:
        pihole.set_client_groups(ip, [_default_group_id])
    except Exception:
        log.exception("Failed to revert bypass for %s", ip)
    with db.get_db() as conn:
        conn.execute(
            "UPDATE bypass_log SET ended_at = datetime('now') "
            "WHERE ip = ? AND ended_at IS NULL",
            (ip,),
        )
    job_id = mac or ip
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)


def _client_info(ip: str) -> tuple[str | None, str]:
    """Return (mac, hostname) for the requesting IP."""
    mac = None
    hostname = ip
    if pihole is None:
        return mac, hostname
    try:
        mac = pihole.get_mac_for_ip(ip)
        data = pihole.get_clients()
        clients = data.get("clients", data) if isinstance(data, dict) else data
        for c in clients:
            if c.get("ip") == ip:
                hostname = c.get("name") or c.get("hostname") or ip
                break
    except Exception:
        log.exception("client_info failed for %s", ip)
    return mac, hostname


# ── Host dispatch ─────────────────────────────────────────────────────────────

@app.route("/")
def index():
    host = request.host.split(":")[0]
    prefix = host.split(".")[0]
    if prefix == "network":
        return _network_view()
    if prefix == "register":
        return _register_view()
    return _me_view()


# ── me.internal ───────────────────────────────────────────────────────────────

def _me_view():
    ip = _client_ip()
    _ensure_pihole()
    mac, hostname = _client_info(ip)
    job_id = mac or ip

    stats = {}
    blocked_domains = []
    blocking = "unknown"
    try:
        client_data = pihole.client_stats(ip)
        stats = _parse_client_stats(client_data, ip)
        blocked_domains = _parse_top_blocked(pihole.top_blocked(ip))
        blocking = pihole.dns_blocking().get("blocking", "unknown")
    except Exception:
        log.exception("Pi-hole stats failed for %s", ip)

    bypass_seconds = _bypass_remaining_seconds(job_id)

    with db.get_db() as conn:
        history = conn.execute(
            "SELECT duration_m, started_at, ended_at, cancelled "
            "FROM bypass_log WHERE ip = ? ORDER BY id DESC LIMIT 10",
            (ip,),
        ).fetchall()

    return render_template(
        "me/index.html",
        ip=ip,
        hostname=hostname,
        mac=mac,
        stats=stats,
        blocked_domains=blocked_domains,
        blocking=blocking,
        bypass_seconds=bypass_seconds,
        bypass_active=bypass_seconds is not None,
        history=history,
        household_name=config.household_name,
    )


@app.route("/status")
def me_status():
    ip = _client_ip()
    mac, _ = _client_info(ip)
    job_id = mac or ip
    bypass_seconds = _bypass_remaining_seconds(job_id)
    blocking = "unknown"
    try:
        blocking = pihole.dns_blocking().get("blocking", "unknown")
    except Exception:
        pass
    return render_template(
        "me/status.html",
        ip=ip,
        blocking=blocking,
        bypass_active=bypass_seconds is not None,
        bypass_seconds=bypass_seconds,
    )


@app.route("/bypass", methods=["POST"])
def me_bypass():
    ip = _client_ip()
    try:
        duration_m = int(request.form.get("duration", 30))
    except ValueError:
        duration_m = 30
    if duration_m not in (15, 30, 60, 120):
        duration_m = 30

    mac, hostname = _client_info(ip)
    job_id = mac or ip

    try:
        pihole.set_client_groups(ip, [_bypass_group_id])
    except Exception:
        log.exception("Bypass group assignment failed for %s", ip)
        return render_template(
            "me/status.html",
            ip=ip,
            blocking="enabled",
            bypass_active=False,
            bypass_seconds=None,
            error="Bypass unavailable — group assignment not supported by this Pi-hole version.",
        ), 503

    run_at = datetime.now(timezone.utc) + timedelta(minutes=duration_m)
    scheduler.add_job(
        _do_revert, "date",
        run_date=run_at,
        id=job_id,
        args=[ip, mac],
        replace_existing=True,
    )

    with db.get_db() as conn:
        conn.execute(
            "INSERT INTO bypass_log (ip, mac, hostname, duration_m) VALUES (?, ?, ?, ?)",
            (ip, mac, hostname, duration_m),
        )

    return render_template(
        "me/status.html",
        ip=ip,
        blocking="enabled",
        bypass_active=True,
        bypass_seconds=duration_m * 60,
    )


@app.route("/bypass/cancel", methods=["POST"])
def me_bypass_cancel():
    ip = _client_ip()
    mac, _ = _client_info(ip)
    job_id = mac or ip

    try:
        pihole.set_client_groups(ip, [_default_group_id])
    except Exception:
        log.exception("Failed to re-enable blocking for %s", ip)

    with db.get_db() as conn:
        conn.execute(
            "UPDATE bypass_log SET ended_at = datetime('now'), cancelled = 1 "
            "WHERE ip = ? AND ended_at IS NULL",
            (ip,),
        )
    if scheduler.get_job(job_id):
        scheduler.remove_job(job_id)

    return render_template(
        "me/status.html",
        ip=ip,
        blocking="enabled",
        bypass_active=False,
        bypass_seconds=None,
    )


@app.route("/rename", methods=["POST"])
def me_rename():
    ip = _client_ip()
    new_name = (request.form.get("name") or "").strip()

    if not HOSTNAME_RE.match(new_name):
        return render_template(
            "me/rename_result.html",
            error="Invalid name. Use letters, numbers, and hyphens (2–30 chars, must start with a letter or digit).",
        )
    if new_name.lower() in RESERVED_NAMES:
        return render_template(
            "me/rename_result.html",
            error=f"'{new_name}' is reserved.",
        )

    try:
        data = pihole.get_clients()
        clients = data.get("clients", data) if isinstance(data, dict) else data

        old_name = None
        for c in clients:
            if c.get("ip") == ip:
                old_name = c.get("name") or c.get("hostname")
                break

        # Collision check
        for c in clients:
            if c.get("ip") != ip:
                if (c.get("name") or "").lower() == new_name.lower():
                    return render_template(
                        "me/rename_result.html",
                        error=f"'{new_name}' is already in use by another device.",
                    )

        if old_name and old_name.lower() == new_name.lower():
            return render_template(
                "me/rename_result.html", success=True, name=new_name, unchanged=True
            )

        # Update Pi-hole client display name
        pihole.update_client(ip, {"name": new_name})

        # Manage custom DNS record: delete old, add new
        fqdn = f"{new_name}.{config.internal_domain}"
        dns_data = pihole.custom_dns_list()
        records = dns_data.get("customdns", dns_data.get("records", []))
        if isinstance(records, dict):
            # Might be {"domain": "ip"} mapping
            records = [{"domain": d, "ip": i} for d, i in records.items()]
        for rec in records:
            if rec.get("ip") == ip and rec.get("domain") != fqdn:
                try:
                    pihole.custom_dns_delete(rec["domain"], ip)
                except Exception:
                    log.warning("Could not delete old DNS record %s", rec["domain"])
        pihole.custom_dns_add(fqdn, ip)

        mac = None
        try:
            mac = pihole.get_mac_for_ip(ip)
        except Exception:
            pass
        with db.get_db() as conn:
            conn.execute(
                "INSERT INTO rename_log (ip, mac, old_name, new_name) VALUES (?, ?, ?, ?)",
                (ip, mac, old_name, new_name),
            )

        return render_template(
            "me/rename_result.html", success=True, name=new_name
        )

    except Exception:
        log.exception("Rename failed for %s", ip)
        return render_template(
            "me/rename_result.html", error="Rename failed — try again."
        )


# ── network.internal stub ─────────────────────────────────────────────────────

def _network_view():
    return render_template(
        "stub.html",
        title="network.internal",
        message="Network dashboard — coming soon.",
        household_name=config.household_name,
    )


# ── register.internal stub ────────────────────────────────────────────────────

def _register_view():
    return render_template(
        "stub.html",
        title="register.internal",
        message="Device registration — coming soon.",
        household_name=config.household_name,
    )


# ── Pi-hole response parsing ──────────────────────────────────────────────────

def _parse_client_stats(data: dict, ip: str) -> dict:
    stats = {"total": 0, "blocked": 0, "percent_blocked": 0.0}
    try:
        clients = data.get("clients", data) if isinstance(data, dict) else data
        for c in clients:
            if c.get("ip") == ip or c.get("name") == ip:
                stats["total"] = c.get("total", 0)
                stats["blocked"] = c.get("blocked", 0)
                if stats["total"]:
                    stats["percent_blocked"] = round(
                        stats["blocked"] / stats["total"] * 100, 1
                    )
                break
    except Exception:
        log.exception("Failed to parse client stats")
    return stats


def _parse_top_blocked(data: dict, count: int = 5) -> list[tuple[str, int]]:
    try:
        domains = data.get("top_blocked_domains", data.get("domains", []))
        if isinstance(domains, dict):
            return sorted(domains.items(), key=lambda x: x[1], reverse=True)[:count]
        if isinstance(domains, list):
            return [(d.get("domain", str(d)), d.get("count", 0)) for d in domains[:count]]
    except Exception:
        log.exception("Failed to parse top blocked domains")
    return []


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    startup()
    app.run(host="127.0.0.1", port=5000, threaded=True)
