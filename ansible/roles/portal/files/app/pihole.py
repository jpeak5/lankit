import logging
import threading

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
        return self._get("/stats/clients", params={"clients": ip})

    def top_blocked(self, ip: str, count: int = 5) -> dict:
        return self._get(
            "/stats/top_blocked_domains",
            params={"blocked": "true", "client": ip, "count": count},
        )

    # ── DNS ───────────────────────────────────────────────────────────────────

    def dns_blocking(self) -> dict:
        return self._get("/dns/blocking")

    def custom_dns_list(self) -> dict:
        return self._get("/customdns/records")

    def custom_dns_add(self, domain: str, ip: str) -> dict:
        return self._post("/customdns/records", {"domain": domain, "ip": ip})

    def custom_dns_delete(self, domain: str, ip: str) -> dict:
        return self._delete("/customdns/records", {"domain": domain, "ip": ip})

    # ── Network ───────────────────────────────────────────────────────────────

    def network_devices(self) -> dict:
        return self._get("/network/devices")

    def get_clients(self) -> dict:
        return self._get("/clients")

    def update_client(self, client_id: str, data: dict) -> dict:
        return self._patch(f"/clients/{client_id}", data)

    # ── Groups ────────────────────────────────────────────────────────────────

    def get_groups(self) -> dict:
        return self._get("/groups")

    def create_group(self, name: str, comment: str = "") -> dict:
        return self._post("/groups", {"name": name, "comment": comment})

    def set_client_groups(self, client_ip: str, group_ids: list[int]) -> dict:
        return self._patch(f"/clients/{client_ip}", {"groups": group_ids})

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
