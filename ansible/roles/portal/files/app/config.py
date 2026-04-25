import os
import tomllib

_path = os.environ.get("LANKIT_CONFIG", "/etc/lankit-portal/config.toml")
with open(_path, "rb") as _f:
    _c = tomllib.load(_f)

pihole_url: str = _c["pihole"]["url"]
pihole_password: str = _c["pihole"]["password"]
household_name: str = _c.get("portal", {}).get("household_name", "Home")
internal_domain: str = _c.get("portal", {}).get("internal_domain", "internal")
