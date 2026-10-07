from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import secrets
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import qrcode
from fastapi import APIRouter, HTTPException, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from qrcode.image.svg import SvgPathImage

from vishing.core.config import Settings, private_ipv4_addresses
from vishing.core.security import hash_secret, new_secret, verify_secret

router = APIRouter()


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


@dataclass
class PairingCode:
    code: str
    expires_at: datetime
    payload: dict[str, Any]
    used: bool = False


@dataclass
class PairingRequest:
    request_id: str
    device_id: str
    device_name: str
    claim_salt: bytes
    claim_hash: bytes
    created_at: datetime = field(default_factory=now)
    status: str = "pending"
    issued_token: str | None = None
    token_delivered: bool = False


class PairingInput(BaseModel):
    pairing_code: str = Field(min_length=6, max_length=32)
    device_id: str = Field(min_length=8, max_length=80)
    device_name: str = Field(min_length=1, max_length=80)
    claim_secret: str = Field(min_length=32, max_length=128)


class DesktopStore:
    def __init__(self, settings: Settings, fingerprint: str):
        self.settings = settings
        self.fingerprint = fingerprint
        identity_file = self.settings.data_dir / "desktop-id"
        if identity_file.exists():
            self.desktop_id = identity_file.read_text(encoding="ascii").strip()
        else:
            self.desktop_id = secrets.token_hex(12)
            identity_file.write_text(self.desktop_id, encoding="ascii")
        self.pairing_code: PairingCode | None = None
        self.pair_requests: dict[str, PairingRequest] = {}
        self.active_sockets: dict[str, WebSocket] = {}
        self.lock = asyncio.Lock()
        self.settings.data_dir.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.settings.database_path) as db:
            db.execute("""CREATE TABLE IF NOT EXISTS paired_devices (
                device_id TEXT PRIMARY KEY,
                device_name TEXT NOT NULL,
                token_salt BLOB NOT NULL,
                token_hash BLOB NOT NULL,
                paired_at TEXT NOT NULL,
                last_seen TEXT,
                revoked INTEGER NOT NULL DEFAULT 0
            )""")

    def create_code(self, address: str | None = None) -> PairingCode:
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
        code = "-".join("".join(secrets.choice(alphabet) for _ in range(4)) for _ in range(2))
        expires = now() + timedelta(minutes=3)
        hosts = private_ipv4_addresses() if self.settings.allow_lan else ["127.0.0.1"]
        if self.settings.allow_lan and not hosts:
            raise HTTPException(status_code=503, detail="No private network address is available. Connect the desktop to Wi-Fi and restart the service.")
        if self.settings.allow_lan and address not in hosts:
            raise HTTPException(status_code=400, detail="Choose one of the private network addresses shown on the desktop dashboard.")
        host = address if self.settings.allow_lan else "127.0.0.1"
        payload = {
            "version": 1,
            "desktop_id": self.desktop_id,
            "desktop_name": "Vishing Detection Desktop",
            "https_url": f"https://{host}:{self.settings.secure_port}",
            "websocket_url": f"wss://{host}:{self.settings.secure_port}/api/v1/mobile/ws",
            "tls_fingerprint": self.fingerprint,
            "pairing_code": code,
            "expires_at": iso(expires),
        }
        self.pairing_code = PairingCode(code, expires, payload)
        self.pair_requests.clear()
        return self.pairing_code

    def list_devices(self) -> list[dict[str, Any]]:
        with sqlite3.connect(self.settings.database_path) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT device_id,device_name,paired_at,last_seen,revoked FROM paired_devices ORDER BY paired_at DESC").fetchall()
        return [{
            "device_id": row["device_id"], "device_name": row["device_name"], "paired_at": row["paired_at"],
            "last_seen": row["last_seen"], "connected": row["device_id"] in self.active_sockets and not row["revoked"],
            "revoked": bool(row["revoked"]),
        } for row in rows]

    def save_device(self, device_id: str, name: str, token: str) -> None:
        salt, digest = hash_secret(token)
        with sqlite3.connect(self.settings.database_path) as db:
            db.execute(
                "INSERT INTO paired_devices(device_id,device_name,token_salt,token_hash,paired_at,last_seen,revoked) VALUES(?,?,?,?,?,?,0) "
                "ON CONFLICT(device_id) DO UPDATE SET device_name=excluded.device_name,token_salt=excluded.token_salt,token_hash=excluded.token_hash,paired_at=excluded.paired_at,last_seen=NULL,revoked=0",
                (device_id, name, salt, digest, iso(now()), None),
            )

    def authenticate(self, device_id: str, token: str) -> bool:
        with sqlite3.connect(self.settings.database_path) as db:
            row = db.execute("SELECT token_salt,token_hash,revoked FROM paired_devices WHERE device_id=?", (device_id,)).fetchone()
        return bool(row and not row[2] and verify_secret(token, row[0], row[1]))

    def touch(self, device_id: str) -> None:
        with sqlite3.connect(self.settings.database_path) as db:
            db.execute("UPDATE paired_devices SET last_seen=? WHERE device_id=? AND revoked=0", (iso(now()), device_id))

    async def revoke(self, device_id: str) -> bool:
        with sqlite3.connect(self.settings.database_path) as db:
            cur = db.execute("UPDATE paired_devices SET revoked=1 WHERE device_id=?", (device_id,))
        socket = self.active_sockets.pop(device_id, None)
        if socket:
            try:
                await socket.close(code=4403, reason="Pairing revoked")
            except (RuntimeError, WebSocketDisconnect):
                # The credential is already revoked, so a concurrent socket
                # disconnect must not turn a successful revoke into an HTTP 500.
                pass
        return cur.rowcount > 0


def get_store(request: Request) -> DesktopStore:
    return request.app.state.store


def require_https(request: Request) -> None:
    if request.url.scheme != "https":
        raise HTTPException(status_code=400, detail="Pairing requires the encrypted HTTPS connection. Scan the desktop QR code.")


def require_loopback(request: Request) -> None:
    host = request.client.host if request.client else ""
    try:
        is_loopback = ipaddress.ip_address(host).is_loopback
    except ValueError:
        is_loopback = False
    if not is_loopback:
        raise HTTPException(status_code=403, detail="Desktop controls are available only on this computer.")
    origin = request.headers.get("origin")
    allowed_origins = {
        f"http://127.0.0.1:{request.app.state.settings.http_port}",
        f"http://localhost:{request.app.state.settings.http_port}",
        f"https://127.0.0.1:{request.app.state.settings.secure_port}",
        f"https://localhost:{request.app.state.settings.secure_port}",
    }
    if origin and origin not in allowed_origins:
        raise HTTPException(status_code=403, detail="Cross-origin requests cannot manage desktop pairings.")


@router.get("/api/v1/health")
def health():
    return {"status": "ok", "service": "vishing-desktop", "protocol_version": 1}


@router.get("/api/v1/desktop/state")
def desktop_state(request: Request):
    require_loopback(request)
    store = get_store(request)
    active = store.pairing_code
    pending = [
        {"request_id": item.request_id, "device_id": item.device_id, "device_name": item.device_name, "created_at": iso(item.created_at)}
        for item in store.pair_requests.values() if item.status == "pending"
    ]
    return {
        "desktop_id": store.desktop_id,
        "certificate_fingerprint": store.fingerprint,
        "secure_port": store.settings.secure_port,
        "allow_lan": request.app.state.settings.allow_lan,
        "allow_lan_requested": request.app.state.allow_lan,
        "lan_addresses": private_ipv4_addresses(),
        "pairing": {
            "active": bool(active and active.expires_at > now() and not active.used),
            "used": bool(active and active.used),
            "expired": bool(active and active.expires_at <= now()),
            "expires_at": iso(active.expires_at) if active else None,
        },
        "pairing_requests": pending,
        "devices": store.list_devices(),
    }


class PairingStartInput(BaseModel):
    address: str | None = None


@router.post("/api/v1/desktop/pairing")
def start_pairing(request: Request, body: PairingStartInput):
    require_loopback(request)
    store = get_store(request)
    if request.app.state.allow_lan != request.app.state.settings.allow_lan:
        raise HTTPException(status_code=409, detail="Restart the desktop service before starting a new phone pairing. Then scan a newly generated QR code.")
    pairing = store.create_code(body.address)
    svg = qrcode.make(json.dumps(pairing.payload, separators=(",", ":")), image_factory=SvgPathImage)
    from io import BytesIO
    import base64

    output = BytesIO()
    svg.save(output)
    return {"payload": pairing.payload, "qr_svg": "data:image/svg+xml;base64," + base64.b64encode(output.getvalue()).decode("ascii")}


@router.post("/api/v1/desktop/pairing/{request_id}/approve")
async def approve_pairing(request: Request, request_id: str):
    require_loopback(request)
    store = get_store(request)
    async with store.lock:
        item = store.pair_requests.get(request_id)
        if not item or item.status != "pending":
            raise HTTPException(status_code=404, detail="Pairing request is no longer available.")
        pairing = store.pairing_code
        if not pairing or pairing.expires_at <= now():
            item.status = "expired"
            raise HTTPException(status_code=410, detail="The pairing request expired. Start a new pairing session.")
        token = new_secret(48)
        store.save_device(item.device_id, item.device_name, token)
        item.issued_token = token
        item.status = "approved"
    return {"status": "approved", "device_id": item.device_id}


@router.post("/api/v1/desktop/pairing/{request_id}/reject")
async def reject_pairing(request: Request, request_id: str):
    require_loopback(request)
    store = get_store(request)
    async with store.lock:
        item = store.pair_requests.get(request_id)
        if not item:
            raise HTTPException(status_code=404, detail="Pairing request is no longer available.")
        item.status = "rejected"
        item.issued_token = None
    return {"status": "rejected"}


@router.delete("/api/v1/desktop/devices/{device_id}")
async def revoke_device(request: Request, device_id: str):
    require_loopback(request)
    if not await get_store(request).revoke(device_id):
        raise HTTPException(status_code=404, detail="Paired device was not found.")
    return {"status": "revoked", "device_id": device_id}


@router.put("/api/v1/desktop/network")
def set_network_access(request: Request, body: dict[str, bool]):
    require_loopback(request)
    allow = bool(body.get("allow_lan", False))
    settings: Settings = request.app.state.settings
    settings.data_dir.mkdir(parents=True, exist_ok=True)
    (settings.data_dir / "connection-settings.json").write_text(json.dumps({"allow_lan": allow}), encoding="utf-8")
    request.app.state.allow_lan = allow
    return {"allow_lan": allow, "restart_required": True}


@router.post("/api/v1/mobile/pairing/request")
async def request_pairing(request: Request, body: PairingInput):
    require_https(request)
    store = get_store(request)
    async with store.lock:
        pairing = store.pairing_code
        if not pairing or pairing.expires_at <= now() or pairing.used:
            raise HTTPException(status_code=410, detail="The desktop pairing code expired. Start a new pairing on the desktop.")
        if not hmac.compare_digest(body.pairing_code, pairing.code):
            raise HTTPException(status_code=403, detail="Pairing code is incorrect.")
        if any(item.device_id == body.device_id and item.status == "pending" for item in store.pair_requests.values()):
            raise HTTPException(status_code=409, detail="This phone already has a pairing request waiting for approval.")
        pairing.used = True
        salt, digest = hash_secret(body.claim_secret)
        request_id = new_secret(18)
        store.pair_requests[request_id] = PairingRequest(request_id, body.device_id, body.device_name.strip(), salt, digest)
    return {"request_id": request_id, "status": "pending"}


@router.get("/api/v1/mobile/status/{device_id}")
def mobile_device_status(request: Request, device_id: str):
    require_https(request)
    authorization = request.headers.get("authorization", "")
    token = authorization.removeprefix("Bearer ")
    if not token or not get_store(request).authenticate(device_id, token):
        raise HTTPException(status_code=403, detail="This phone is not paired or its pairing was revoked.")
    return {"status": "paired", "device_id": device_id}


class PairingStatusInput(BaseModel):
    claim_secret: str = Field(min_length=32, max_length=128)


@router.post("/api/v1/mobile/pairing/{request_id}")
def pairing_status(request: Request, request_id: str, body: PairingStatusInput):
    require_https(request)
    store = get_store(request)
    item = store.pair_requests.get(request_id)
    if not item or not verify_secret(body.claim_secret, item.claim_salt, item.claim_hash):
        raise HTTPException(status_code=404, detail="Pairing request was not found.")
    if item.status == "approved" and item.issued_token and not item.token_delivered:
        token = item.issued_token
        item.issued_token = None
        item.token_delivered = True
        return {"status": "approved", "device_id": item.device_id, "device_token": token}
    return {"status": item.status}


@router.websocket("/api/v1/mobile/ws")
async def mobile_socket(websocket: WebSocket):
    if websocket.scope.get("scheme") != "wss":
        await websocket.close(code=4400, reason="Encrypted WebSocket is required")
        return
    authorization = websocket.headers.get("authorization", "")
    device_id = websocket.headers.get("x-device-id", "")
    token = authorization.removeprefix("Bearer ")
    store: DesktopStore = websocket.app.state.store
    if not device_id or not token or not store.authenticate(device_id, token):
        await websocket.close(code=4401, reason="Device authentication failed")
        return
    await websocket.accept()
    previous = store.active_sockets.get(device_id)
    if previous:
        await previous.close(code=4409, reason="A newer connection replaced this session")
    store.active_sockets[device_id] = websocket
    store.touch(device_id)
    await websocket.send_json({"type": "connected", "protocol_version": 1, "device_id": device_id, "server_time": iso(now())})
    try:
        while True:
            message = await websocket.receive_json()
            if not isinstance(message, dict) or message.get("type") != "ping":
                await websocket.close(code=4400, reason="Unsupported connection message")
                break
            store.touch(device_id)
            await websocket.send_json({"type": "pong", "server_time": iso(now())})
    except WebSocketDisconnect:
        pass
    except (ValueError, json.JSONDecodeError):
        await websocket.close(code=4400, reason="Invalid message")
    finally:
        if store.active_sockets.get(device_id) is websocket:
            store.active_sockets.pop(device_id, None)
