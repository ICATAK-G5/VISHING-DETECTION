from __future__ import annotations

import ipaddress
import os
import socket
from dataclasses import dataclass
from pathlib import Path


def _data_root() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / ".local" / "share")
    return Path(base) / "VishingDetection"


def private_ipv4_addresses() -> list[str]:
    found: set[str] = set()
    try:
        for entry in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            address = entry[4][0]
            parsed = ipaddress.ip_address(address)
            if parsed.is_private and not parsed.is_loopback and not parsed.is_link_local:
                found.add(address)
    except OSError:
        pass
    return sorted(found)


@dataclass(frozen=True)
class Settings:
    http_host: str = "127.0.0.1"
    http_port: int = 8000
    secure_port: int = 8443
    data_dir: Path = _data_root()
    dashboard_dir: Path = Path(__file__).resolve().parents[1] / "dashboard"
    allow_lan: bool = False

    @property
    def certificate_path(self) -> Path:
        return self.data_dir / "desktop-cert.pem"

    @property
    def private_key_path(self) -> Path:
        return self.data_dir / "desktop-key.pem"

    @property
    def database_path(self) -> Path:
        return self.data_dir / "paired-devices.sqlite3"


settings = Settings(
    http_port=int(os.environ.get("VISHING_HTTP_PORT", "8000")),
    secure_port=int(os.environ.get("VISHING_SECURE_PORT", "8443")),
)

