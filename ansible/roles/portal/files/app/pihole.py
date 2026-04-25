import logging
import threading
from urllib.parse import quote

import requests

log = logging.getLogger(__name__)


class PiholeClient:
    def __init__(self, url: str, password: str):
        self.base = url.rstrip("/") + "/api"
        self.password = password
        self.token: str | None = None
        self._lock = threading.Lock()
        self._authenticate()

    # ── Auth ──────────────────────────────────────────────────────────────────

    def _authenticate(self):
        r = requests.post(
            f"{self.base}/auth",
            json={"password": self.password},
            timeout=5,
        )
        r.raise_for_status()
        self.token = r.json()["session"]["sid"]
        log.info("Pi-hole authenticated")

    def _headers(self) -> dict:
        return {"X-FTL-SID": self.token} if self.token else {}

    # ── HTTP verbs ────────────────────────────────────────────────────────────

    def _get(self, path: str, **kwargs):
        with self._lock:
            r = requests.get(
                f"{self.base}{path}", headers=self._headers(), timeout=5, **kwargs
            )
            if r.status_code == 401:
                self._authenticate()
                r = requests.get(
                    f"{self.base}{path}", headers=self._headers(), timeout=5, **kwargs
                )
            r.raise_for_status()
            return r.json()

    def _post(self, path: str, data: dict):
        with self._lock:
            r = requests.post(
                f"{self.base}{path}", json=data, headers=self._headers(), timeout=5
            )
            if r.status_code == 401:
                self._authenticate()
                r = requests.post(
                    f"{self.base}{path}", json=data, headers=self._headers(), timeout=5
                )
            r.raise_for_status()
            return r.json()

    def _put(self, path: str, data: dict | None = None):
        with self._lock:
            r = requests.put(
                f"{self.base}{path}", json=data, headers=self._headers(), timeout=5
            )
            if r.status_code == 401:
                self._authenticate()
                r = requests.put(
                    f"{self.base}{path}", json=data, headers=self._headers(), timeout=5
                )
            r.raise_for_status()
            return r.json() if r.content else {}

    def _patch(self, path: str, data: dict):
        with self._lock:
            r = requests.patch(
                f"{self.base}{path}", json=data, headers=self._headers(), timeout=5
            )
            if r.status_code == 401:
                self._authenticate()
                r = requests.patch(
                    f"{self.base}{path}", json=data, headers=self._headers(), timeout=5
                )
            r.raise_for_status()
            return r.json()

    def _delete(self, path: str, data: dict | None = None):
        with self._lock:
            r = requests.delete(
                f"{self.base}{path}",
                json=data,
                headers=self._headers(),
                timeout=5,
            )
            if r.status_code == 401:
                self._authenticate()
                r = requests.delete(
                    f"{self.base}{path}",
                    json=data,
                    headers=self._headers(),
                    timeout=5,
                )
            r.raise_for_status()
            return r.json() if r.content else {}

    # ── Stats ─────────────────────────────────────────────────────────────────

    def stats_summary(self) -> dict:
        return self._get("/stats/summary")

    def client_stats(self, ip: str) -> dict:
        """Return {total, blocked, percent_blocked} for a specific client IP."""
        total = 0
        blocked = 0
        data = self._get("/stats/top_clients", params={"count": 500})
        for c in data.get("clients", []):
            if c.get("ip") == ip:
                total = c.get("count", 0)
                break
        data_blocked = self._get("/stats/top_clients", params={"count": 500, "blocked": "true"})
        for c in data_blocked.get("clients", []):
            if c.get("ip") == ip:
                blocked = c.get("count", 0)
                break
        pct = round(blocked / total * 100, 1) if total else 0.0
        return {"total": total, "blocked": blocked, "percent_blocked": pct}

    def top_blocked(self, ip: str, count: int = 10) -> list[tuple[str, int]]:
        """Return top blocked (domain, count) pairs for a client IP."""
        data = self._get("/queries", params={
            "client_ip": ip,
            "upstream": "blocklist",
            "length": 500,
        })
        domain_counts: dict[str, int] = {}
        for q in data.get("queries", []):
            d = q.get("domain") or ""
            if d and d != "hidden":
                domain_counts[d] = domain_counts.get(d, 0) + 1
        return sorted(domain_counts.items(), key=lambda x: x[1], reverse=True)[:count]

    # ── DNS ───────────────────────────────────────────────────────────────────

    def dns_blocking(self) -> dict:
        return self._get("/dns/blocking")

    def custom_dns_list(self) -> list[str]:
        """Return current local DNS host entries as ['IP hostname', ...]."""
        data = self._get("/config/dns/hosts")
        return data.get("config", {}).get("dns", {}).get("hosts", [])

    def custom_dns_add(self, domain: str, ip: str) -> dict:
        entry = quote(f"{ip} {domain}", safe="")
        return self._put(f"/config/dns/hosts/{entry}")

    def custom_dns_delete(self, domain: str, ip: str) -> dict:
        entry = quote(f"{ip} {domain}", safe="")
        return self._delete(f"/config/dns/hosts/{entry}")

    # ── Network ───────────────────────────────────────────────────────────────

    def network_devices(self) -> dict:
        return self._get("/network/devices")

    def get_clients(self) -> dict:
        return self._get("/clients")

    def update_client(self, client_ip: str, data: dict) -> dict:
        return self._put(f"/clients/ip-{client_ip}", data)

    # ── Groups ────────────────────────────────────────────────────────────────

    def get_groups(self) -> dict:
        return self._get("/groups")

    def create_group(self, name: str, comment: str = "") -> dict:
        return self._post("/groups", {"name": name, "comment": comment})

    def set_client_groups(self, client_ip: str, group_ids: list[int]) -> dict:
        return self._put(f"/clients/ip-{client_ip}", {"groups": group_ids})

    # ── Convenience ───────────────────────────────────────────────────────────

    def get_mac_for_ip(self, ip: str) -> str | None:
        """Return the MAC address for a given IP, or None."""
        data = self.network_devices()
        devices = data.get("devices", data) if isinstance(data, dict) else data
        for d in devices:
            ips = d.get("ips", [d.get("ip", "")])
            if isinstance(ips, str):
                ips = [ips]
            if ip in ips:
                return d.get("hwaddr") or d.get("mac")
        return None
