import logging
import re
import threading
import time
from datetime import datetime, timedelta, timezone

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from flask import Flask, abort, render_template, request

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
SPEEDTEST_COOLDOWN_S = 15 * 60

_speedtest_running = threading.Event()
_speedtest_lock = threading.Lock()


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


def _host_prefix() -> str:
    return request.host.split(":")[0].split(".")[0]


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
        # DNS records are the canonical name source (set via rename)
        for entry in pihole.custom_dns_list():
            parts = entry.split()
            if len(parts) == 2 and parts[0] == ip:
                hostname = parts[1].split(".")[0]
                return mac, hostname
        # Fall back to Pi-hole client metadata
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
        stats = pihole.client_stats(ip)
        blocked_domains = pihole.top_blocked(ip)
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
        privacy_hidden=config.query_privacy_level > 0 and not blocked_domains and bool(stats.get("blocked")),
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
        fqdn = f"{new_name}.{config.internal_domain}"

        # DNS records are authoritative: check for collision and find current name
        old_fqdn = None
        dns_records = pihole.custom_dns_list()
        for entry in dns_records:
            parts = entry.split()
            if len(parts) != 2:
                continue
            entry_ip, entry_domain = parts
            if entry_ip == ip:
                old_fqdn = entry_domain
            elif entry_domain == fqdn:
                return render_template(
                    "me/rename_result.html",
                    error=f"'{new_name}' is already in use by another device.",
                )

        if old_fqdn and old_fqdn == fqdn:
            return render_template(
                "me/rename_result.html", success=True, name=new_name, unchanged=True
            )

        # Delete old DNS record and add new one
        if old_fqdn:
            try:
                pihole.custom_dns_delete(old_fqdn, ip)
            except Exception:
                log.warning("Could not delete old DNS record %s", old_fqdn)
        pihole.custom_dns_add(fqdn, ip)

        # Store name as comment for Pi-hole admin visibility
        try:
            pihole.update_client(ip, {"comment": new_name})
        except Exception:
            log.warning("Could not update Pi-hole client comment for %s", ip)

        old_name = old_fqdn.split(".")[0] if old_fqdn else None
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


# ── network.internal ──────────────────────────────────────────────────────────

def _latency_data() -> dict:
    result = {}
    for target in ["1.1.1.1", "8.8.8.8"]:
        with db.get_db() as conn:
            rows = conn.execute(
                "SELECT rtt_ms, measured_at FROM latency_log "
                "WHERE target=? ORDER BY id DESC LIMIT 12",
                (target,),
            ).fetchall()
        if not rows:
            continue
        valid = [r["rtt_ms"] for r in rows if r["rtt_ms"] is not None]
        avg = round(sum(valid) / len(valid), 1) if valid else None
        trend = "stable"
        if len(valid) >= 8:
            newer_avg = sum(valid[:4]) / 4
            older_avg = sum(valid[4:8]) / 4
            if newer_avg < older_avg * 0.9:
                trend = "improving"
            elif newer_avg > older_avg * 1.1:
                trend = "degrading"
        result[target] = {"avg": avg, "last_at": rows[0]["measured_at"], "trend": trend}
    return result


def _get_device_list() -> list[dict]:
    now = time.time()
    leases = pihole.dhcp_leases().get("leases", [])
    online_ips = {l["ip"] for l in leases if l.get("expires", 0) > now}

    dns_names = {}
    for entry in pihole.custom_dns_list():
        parts = entry.split()
        if len(parts) == 2:
            dns_names[parts[0]] = parts[1].split(".")[0]

    devices = []
    seen_ips: set[str] = set()
    for d in pihole.network_devices().get("devices", []):
        ips = d.get("ips", [d.get("ip", "")])
        if isinstance(ips, str):
            ips = [ips]
        mac = d.get("hwaddr") or d.get("mac")
        for ip in ips:
            if not ip or ip in seen_ips:
                continue
            seen_ips.add(ip)
            hostname = dns_names.get(ip) or d.get("hostname") or ip
            devices.append({"ip": ip, "hostname": hostname, "mac": mac, "online": ip in online_ips})

    devices.sort(key=lambda x: (not x["online"], x["hostname"].lower()))
    return devices


def _run_speedtest():
    import speedtest as st_lib
    with db.get_db() as conn:
        row_id = conn.execute(
            "INSERT INTO speed_results (error) VALUES ('running')"
        ).lastrowid
    try:
        st = st_lib.Speedtest(secure=True)
        st.get_best_server()
        st.download()
        st.upload()
        r = st.results.dict()
        with db.get_db() as conn:
            conn.execute(
                "UPDATE speed_results SET download_mbps=?, upload_mbps=?, ping_ms=?, error=NULL "
                "WHERE id=?",
                (r["download"] / 1e6, r["upload"] / 1e6, r["ping"], row_id),
            )
    except Exception as e:
        with db.get_db() as conn:
            conn.execute(
                "UPDATE speed_results SET error=? WHERE id=?",
                (str(e), row_id),
            )
    finally:
        _speedtest_running.clear()


def _network_view():
    _ensure_pihole()
    latency = _latency_data()
    with db.get_db() as conn:
        last_speed = conn.execute(
            "SELECT * FROM speed_results WHERE error != 'running' OR error IS NULL "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return render_template(
        "network/index.html",
        latency=latency,
        last_speed=last_speed,
        speedtest_running=_speedtest_running.is_set(),
        household_name=config.household_name,
    )


@app.route("/devices")
def network_devices_fragment():
    if _host_prefix() != "network":
        abort(404)
    _ensure_pihole()
    devices = []
    try:
        devices = _get_device_list()
    except Exception:
        log.exception("Failed to build device list")
    return render_template("network/devices.html", devices=devices)


@app.route("/speedtest", methods=["POST"])
def network_speedtest_trigger():
    if _host_prefix() != "network":
        abort(404)
    with _speedtest_lock:
        if _speedtest_running.is_set():
            return render_template(
                "network/speedtest_status.html",
                speedtest_running=True, last_speed=None,
            )
        with db.get_db() as conn:
            last = conn.execute(
                "SELECT measured_at FROM speed_results "
                "WHERE error != 'running' OR error IS NULL ORDER BY id DESC LIMIT 1"
            ).fetchone()
        if last:
            from datetime import datetime
            last_dt = datetime.fromisoformat(last["measured_at"])
            age_s = (datetime.utcnow() - last_dt).total_seconds()
            if age_s < SPEEDTEST_COOLDOWN_S:
                wait_m = int((SPEEDTEST_COOLDOWN_S - age_s) / 60) + 1
                with db.get_db() as conn:
                    last_speed = conn.execute(
                        "SELECT * FROM speed_results WHERE error != 'running' OR error IS NULL "
                        "ORDER BY id DESC LIMIT 1"
                    ).fetchone()
                return render_template(
                    "network/speedtest_status.html",
                    speedtest_running=False,
                    last_speed=last_speed,
                    cooldown_m=wait_m,
                )
        _speedtest_running.set()
        threading.Thread(target=_run_speedtest, daemon=True).start()

    return render_template(
        "network/speedtest_status.html",
        speedtest_running=True, last_speed=None,
    )


@app.route("/speedtest/status")
def network_speedtest_status():
    if _host_prefix() != "network":
        abort(404)
    with db.get_db() as conn:
        last_speed = conn.execute(
            "SELECT * FROM speed_results WHERE error != 'running' OR error IS NULL "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return render_template(
        "network/speedtest_status.html",
        speedtest_running=_speedtest_running.is_set(),
        last_speed=last_speed,
    )


# ── register.internal ─────────────────────────────────────────────────────────

def _register_view():
    ip = _client_ip()
    _ensure_pihole()
    mac = None
    current_name = None
    try:
        mac = pihole.get_mac_for_ip(ip)
        for entry in pihole.custom_dns_list():
            parts = entry.split()
            if len(parts) == 2 and parts[0] == ip:
                current_name = parts[1].split(".")[0]
                break
    except Exception:
        log.exception("register_view lookup failed for %s", ip)
    return render_template(
        "register/index.html",
        ip=ip,
        mac=mac,
        current_name=current_name,
        household_name=config.household_name,
    )


@app.route("/register", methods=["POST"])
def register_device():
    if _host_prefix() != "register":
        abort(404)
    ip = _client_ip()
    new_name = (request.form.get("name") or "").strip()

    if not HOSTNAME_RE.match(new_name):
        return render_template(
            "register/result.html",
            error="Invalid name. Use letters, numbers, and hyphens (1–30 chars).",
        )
    if new_name.lower() in RESERVED_NAMES:
        return render_template(
            "register/result.html",
            error=f"'{new_name}' is reserved.",
        )

    try:
        fqdn = f"{new_name}.{config.internal_domain}"
        old_fqdn = None
        for entry in pihole.custom_dns_list():
            parts = entry.split()
            if len(parts) != 2:
                continue
            entry_ip, entry_domain = parts
            if entry_ip == ip:
                old_fqdn = entry_domain
            elif entry_domain == fqdn:
                return render_template(
                    "register/result.html",
                    error=f"'{new_name}' is already in use by another device.",
                )

        if old_fqdn and old_fqdn == fqdn:
            return render_template(
                "register/result.html",
                success=True, name=new_name, unchanged=True,
            )

        if old_fqdn:
            try:
                pihole.custom_dns_delete(old_fqdn, ip)
            except Exception:
                log.warning("Could not delete old DNS record %s", old_fqdn)
        pihole.custom_dns_add(fqdn, ip)
        try:
            pihole.update_client(ip, {"comment": new_name})
        except Exception:
            log.warning("Could not update Pi-hole client comment for %s", ip)

        mac = None
        try:
            mac = pihole.get_mac_for_ip(ip)
        except Exception:
            pass

        return render_template(
            "register/result.html",
            success=True, name=new_name, ip=ip,
            domain=config.internal_domain,
        )

    except Exception:
        log.exception("Registration failed for %s", ip)
        return render_template(
            "register/result.html",
            error="Registration failed — try again.",
        )


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    startup()
    app.run(host="127.0.0.1", port=5000, threaded=True)
