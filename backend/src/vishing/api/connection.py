from __future__ import annotations

import asyncio
import hmac
import ipaddress
import json
import secrets
import sqlite3
import struct
import time
import wave
import io
import math
import uuid
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import qrcode
from fastapi import APIRouter, File, Form, HTTPException, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from qrcode.image.svg import SvgPathImage

from vishing.core.config import Settings, private_ipv4_addresses
from vishing.core.security import hash_secret, new_secret, verify_secret

router = APIRouter()
MAX_AUDIO_MONITORS = 8
MAX_VOICE_MESSAGE_BYTES = 25 * 1024 * 1024
MAX_STORED_VOICE_MESSAGE_BYTES = 250 * 1024 * 1024
MAX_VOICE_MESSAGE_SECONDS = 10 * 60
VOICE_MESSAGE_RETENTION_SECONDS = 24 * 60 * 60
PCM_FRAME_BYTES = 640


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


@dataclass
class AudioSession:
    device_id: str
    device_name: str
    session_id: str | None = None
    source: str = "cellular_microphone"
    status: str = "armed"
    sample_rate: int = 16000
    channels: int = 1
    sample_format: str = "pcm_s16le"
    frame_duration_ms: int = 20
    frame_bytes: int = 640
    frames_received: int = 0
    bytes_received: int = 0
    dropped_frames: int = 0
    sequence_gaps: int = 0
    last_sequence: int = -1
    last_capture_ns: int = 0
    last_frame_at: datetime | None = None
    last_rms: float = 0.0
    last_peak: int = 0
    jitter_ewma_ms: float = 0.0
    round_trip_ms: float | None = None
    estimated_one_way_ms: float | None = None
    previous_arrival_ns: int | None = None
    last_store_touch_ns: int = 0
    started_at: datetime = field(default_factory=now)
    ended_at: datetime | None = None
    # Sixty seconds of mono 16 kHz PCM16 is about 1.92 MB per stream.
    preview_frames: deque[bytes] = field(default_factory=lambda: deque(maxlen=3000))
    stream_key: str | None = None
    call_id: str | None = None
    role: str | None = None


@dataclass
class ControlledCall:
    call_id: str
    far_device_id: str
    near_device_id: str
    far_device_name: str
    near_device_name: str
    created_at: datetime = field(default_factory=now)
    status: str = "ringing"

    def role_for(self, device_id: str) -> str | None:
        if device_id == self.far_device_id:
            return "far"
        if device_id == self.near_device_id:
            return "near"
        return None

    def peer_for(self, device_id: str) -> str | None:
        if device_id == self.far_device_id:
            return self.near_device_id
        if device_id == self.near_device_id:
            return self.far_device_id
        return None


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
        self.audio_sockets: dict[str, WebSocket] = {}
        self.audio_sessions: dict[str, AudioSession] = {}
        self.webrtc_sessions: dict[str, AudioSession] = {}
        self.webrtc_sockets: dict[str, WebSocket] = {}
        # Ephemeral two-phone call signaling rooms. Media stays peer-to-peer.
        self.controlled_calls: dict[str, ControlledCall] = {}
        self.audio_stream_sessions: dict[str, AudioSession] = {}
        self.audio_stream_sockets: dict[str, WebSocket] = {}
        self.voice_message_dir = self.settings.data_dir / "voice-messages"
        self.voice_message_lock = asyncio.Lock()
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
            db.execute("""CREATE TABLE IF NOT EXISTS voice_messages (
                message_id TEXT PRIMARY KEY,
                device_id TEXT NOT NULL,
                device_name TEXT NOT NULL,
                call_id TEXT NOT NULL,
                file_name TEXT NOT NULL,
                created_at TEXT NOT NULL,
                duration_seconds REAL NOT NULL,
                file_size INTEGER NOT NULL
            )""")
        self.voice_message_dir.mkdir(parents=True, exist_ok=True)
        self.expire_voice_messages()

    def expire_voice_messages(self) -> None:
        cutoff = (now() - timedelta(seconds=VOICE_MESSAGE_RETENTION_SECONDS)).isoformat()
        with sqlite3.connect(self.settings.database_path) as db:
            expired = db.execute("SELECT message_id,file_name FROM voice_messages WHERE created_at < ?", (cutoff,)).fetchall()
            for _, file_name in expired:
                (self.voice_message_dir / file_name).unlink(missing_ok=True)
            db.execute("DELETE FROM voice_messages WHERE created_at < ?", (cutoff,))

    def list_voice_messages(self) -> list[dict[str, Any]]:
        self.expire_voice_messages()
        with sqlite3.connect(self.settings.database_path) as db:
            db.row_factory = sqlite3.Row
            rows = db.execute("SELECT * FROM voice_messages ORDER BY created_at DESC").fetchall()
        return [{key: row[key] for key in row.keys()} for row in rows]

    def add_voice_message(self, device_id: str, device_name: str, call_id: str, file_name: str, created_at: str, duration_seconds: float, file_size: int) -> None:
        with sqlite3.connect(self.settings.database_path) as db:
            db.execute("INSERT INTO voice_messages VALUES(?,?,?,?,?,?,?,?)", (uuid.uuid4().hex, device_id, device_name, call_id, file_name, created_at, duration_seconds, file_size))

    def delete_voice_message(self, message_id: str) -> bool:
        with sqlite3.connect(self.settings.database_path) as db:
            row = db.execute("SELECT file_name FROM voice_messages WHERE message_id=?", (message_id,)).fetchone()
            if not row:
                return False
            (self.voice_message_dir / row[0]).unlink(missing_ok=True)
            db.execute("DELETE FROM voice_messages WHERE message_id=?", (message_id,))
            return True

    def ingest_pcm_frame(self, session: AudioSession, pcm: bytes, sequence: int, capture_ns: int) -> None:
        arrival_ns = time.monotonic_ns()
        if session.previous_arrival_ns is not None:
            delta_ms = (arrival_ns - session.previous_arrival_ns) / 1_000_000
            deviation = abs(delta_ms - session.frame_duration_ms)
            session.jitter_ewma_ms = deviation if session.frames_received == 0 else (0.2 * deviation + 0.8 * session.jitter_ewma_ms)
        samples = struct.unpack("<320h", pcm)
        session.last_peak = max((abs(sample) for sample in samples), default=0)
        session.last_rms = math.sqrt(sum(sample * sample for sample in samples) / len(samples)) / 32768.0
        if session.last_sequence >= 0 and sequence > session.last_sequence + 1:
            session.sequence_gaps += sequence - session.last_sequence - 1
        session.frames_received += 1
        session.bytes_received += len(pcm)
        session.last_sequence = sequence
        session.last_capture_ns = capture_ns
        session.last_frame_at = now()
        session.previous_arrival_ns = arrival_ns
        session.preview_frames.append(pcm)

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
            "last_seen": row["last_seen"], "connected": (row["device_id"] in self.active_sockets or row["device_id"] in self.audio_sockets) and not row["revoked"],
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
        audio_socket = self.audio_sockets.pop(device_id, None)
        if audio_socket:
            try:
                await audio_socket.close(code=4403, reason="Pairing revoked")
            except (RuntimeError, WebSocketDisconnect):
                pass
        for key, socket in list(self.webrtc_sockets.items()):
            if key.startswith(f"{device_id}:"):
                self.webrtc_sockets.pop(key, None)
                try:
                    await socket.close(code=4403, reason="Pairing revoked")
                except (RuntimeError, WebSocketDisconnect):
                    pass
        for call_id, call in list(self.controlled_calls.items()):
            if device_id not in {call.far_device_id, call.near_device_id}:
                continue
            for participant_id in {call.far_device_id, call.near_device_id}:
                participant = self.active_sockets.get(participant_id)
                if participant:
                    try:
                        await participant.send_json({"type": "call.ended", "call_id": call_id, "reason": "A paired device was revoked."})
                    except (RuntimeError, WebSocketDisconnect):
                        pass
            self.controlled_calls.pop(call_id, None)
            for key, session in list(self.audio_stream_sessions.items()):
                if session.call_id == call_id:
                    session.status = "revoked"
                    session.ended_at = now()
                    stream_socket = self.audio_stream_sockets.pop(key, None)
                    if stream_socket:
                        try:
                            await stream_socket.close(code=4403, reason="Pairing revoked")
                        except (RuntimeError, WebSocketDisconnect):
                            pass
        for session in self.webrtc_sessions.values():
            if session.device_id == device_id:
                session.status = "revoked"
                session.ended_at = now()
        session = self.audio_sessions.get(device_id)
        if session:
            session.status = "revoked"
            session.ended_at = now()
        return cur.rowcount > 0

    def audio_snapshot(self, device_id: str) -> dict[str, Any] | None:
        session = self.audio_sessions.get(device_id) or self.webrtc_sessions.get(device_id) or self.audio_stream_sessions.get(device_id)
        if not session:
            return None
        return {
            "device_id": session.device_id,
            "device_name": session.device_name,
            "session_id": session.session_id,
            "source": session.source,
            "status": session.status,
            "sample_rate": session.sample_rate,
            "channels": session.channels,
            "sample_format": session.sample_format,
            "frame_duration_ms": session.frame_duration_ms,
            "frames_received": session.frames_received,
            "bytes_received": session.bytes_received,
            "dropped_frames": session.dropped_frames,
            "sequence_gaps": session.sequence_gaps,
            "last_rms": round(session.last_rms, 3),
            "last_peak": session.last_peak,
            "jitter_ewma_ms": round(session.jitter_ewma_ms, 2),
            "round_trip_ms": round(session.round_trip_ms, 2) if session.round_trip_ms is not None else None,
            "estimated_one_way_ms": round(session.estimated_one_way_ms, 2) if session.estimated_one_way_ms is not None else None,
            "last_frame_at": iso(session.last_frame_at),
            "started_at": iso(session.started_at),
            "ended_at": iso(session.ended_at),
            "preview_available": bool(session.preview_frames),
            "stream_key": session.stream_key or session.device_id,
            "call_id": session.call_id,
            "role": session.role,
        }


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
        "audio_sessions": [
            snapshot for device_id in store.audio_sessions
            if (snapshot := store.audio_snapshot(device_id)) is not None
        ] + [snapshot for session in store.webrtc_sessions.values() if (snapshot := store.audio_snapshot(session.stream_key or "")) is not None] + [
            snapshot for key in store.audio_stream_sessions if (snapshot := store.audio_snapshot(key)) is not None
        ],
        "voice_messages": store.list_voice_messages(),
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
    peers = [item for item in store.list_devices() if item["device_id"] != device_id and not item["revoked"]]
    await websocket.send_json({
        "type": "connected", "protocol_version": 1, "device_id": device_id,
        "server_time": iso(now()),
        "paired_phones": [{"device_id": item["device_id"], "device_name": item["device_name"], "connected": item["connected"]} for item in peers],
    })
    try:
        while True:
            message = await websocket.receive_json()
            if not isinstance(message, dict) or message.get("protocol_version", 1) != 1:
                await websocket.close(code=4400, reason="Invalid connection message")
                break
            kind = message.get("type")
            if kind == "ping":
                store.touch(device_id)
                await websocket.send_json({"type": "pong", "server_time": iso(now())})
                continue
            if kind == "devices.list":
                peers = [item for item in store.list_devices() if item["device_id"] != device_id and not item["revoked"]]
                await websocket.send_json({"type": "devices.updated", "paired_phones": [{"device_id": item["device_id"], "device_name": item["device_name"], "connected": item["connected"]} for item in peers]})
                continue
            if kind == "call.request":
                for expired_id, expired_call in list(store.controlled_calls.items()):
                    if expired_call.status == "ringing" and (now() - expired_call.created_at).total_seconds() > 45:
                        store.controlled_calls.pop(expired_id, None)
                        expired_caller = store.active_sockets.get(expired_call.far_device_id)
                        if expired_caller:
                            await expired_caller.send_json({"type": "call.ended", "protocol_version": 1, "call_id": expired_id, "reason": "The call request timed out."})
                near_device_id = message.get("peer_device_id")
                devices = {item["device_id"]: item for item in store.list_devices()}
                near_device = devices.get(near_device_id)
                if near_device_id == device_id or not near_device or near_device["revoked"]:
                    await websocket.send_json({"type": "call.error", "detail": "Choose another valid paired phone."})
                    continue
                if device_id not in store.active_sockets or near_device_id not in store.active_sockets:
                    await websocket.send_json({"type": "call.error", "detail": "Both phones must be connected to the desktop."})
                    continue
                if near_device_id == device_id or len(store.controlled_calls) >= 8:
                    await websocket.send_json({"type": "call.error", "detail": "The call service is busy or the selected phone is invalid."})
                    continue
                if any(item.status in {"ringing", "active", "connected"} and {item.far_device_id, item.near_device_id} & {device_id, near_device_id} for item in store.controlled_calls.values()):
                    await websocket.send_json({"type": "call.error", "detail": "One of these phones is already in another call."})
                    continue
                far_device = devices[device_id]
                call_id = f"call_{secrets.token_hex(12)}"
                call = ControlledCall(call_id, device_id, near_device_id, far_device["device_name"], near_device["device_name"])
                store.controlled_calls[call_id] = call
                await websocket.send_json({"type": "call.ringing", "protocol_version": 1, "call_id": call_id, "role": "far", "peer_device_id": near_device_id, "peer_name": near_device["device_name"]})
                await store.active_sockets[near_device_id].send_json({"type": "call.invite", "protocol_version": 1, "call_id": call_id, "role": "near", "peer_device_id": device_id, "peer_name": far_device["device_name"]})
                continue
            call_id = message.get("call_id")
            call = store.controlled_calls.get(call_id) if isinstance(call_id, str) else None
            role = call.role_for(device_id) if call else None
            peer_id = call.peer_for(device_id) if call else None
            if not call or not role or not peer_id:
                await websocket.send_json({"type": "call.error", "call_id": call_id, "detail": "This call is unknown, expired, or not assigned to this phone."})
                continue
            peer_socket = store.active_sockets.get(peer_id)
            if kind == "call.accept":
                if role != "near" or call.status != "ringing":
                    await websocket.send_json({"type": "call.error", "call_id": call_id, "detail": "This call cannot be accepted by this phone."})
                    continue
                call.status = "active"
                await websocket.send_json({"type": "call.ready", "protocol_version": 1, "call_id": call_id, "role": role, "peer_device_id": peer_id, "peer_name": call.far_device_name})
                if peer_socket:
                    await peer_socket.send_json({"type": "call.ready", "protocol_version": 1, "call_id": call_id, "role": "far", "peer_device_id": device_id, "peer_name": call.near_device_name})
                continue
            if kind == "call.reject":
                if peer_socket:
                    await peer_socket.send_json({"type": "call.rejected", "protocol_version": 1, "call_id": call_id})
                store.controlled_calls.pop(call_id, None)
                for key, session in list(store.audio_stream_sessions.items()):
                    if session.call_id == call_id:
                        session.status = "stopped"
                        session.ended_at = now()
                        stream_socket = store.audio_stream_sockets.pop(key, None)
                        if stream_socket:
                            try:
                                await stream_socket.close(code=1000, reason="Call signaling disconnected")
                            except (RuntimeError, WebSocketDisconnect):
                                pass
                continue
            if kind == "call.end":
                for participant_id in {call.far_device_id, call.near_device_id}:
                    participant = store.active_sockets.get(participant_id)
                    if participant:
                        await participant.send_json({"type": "call.ended", "protocol_version": 1, "call_id": call_id})
                store.controlled_calls.pop(call_id, None)
                for key, session in list(store.audio_stream_sessions.items()):
                    if session.call_id == call_id:
                        session.status = "stopped"
                        session.ended_at = now()
                        stream_socket = store.audio_stream_sockets.pop(key, None)
                        if stream_socket:
                            try:
                                await stream_socket.close(code=1000, reason="Call ended")
                            except (RuntimeError, WebSocketDisconnect):
                                pass
                continue
            if kind in {"call.offer", "call.answer", "call.ice", "call.connected"}:
                if call.status not in {"active", "connected"} or not peer_socket:
                    await websocket.send_json({"type": "call.error", "call_id": call_id, "detail": "The other phone is not available for call signaling."})
                    continue
                if (kind == "call.offer" and role != "near") or (kind == "call.answer" and role != "far"):
                    await websocket.close(code=4403, reason="Call signaling role mismatch")
                    break
                if kind == "call.offer" or kind == "call.answer":
                    sdp = message.get("sdp")
                    if not isinstance(sdp, str) or not sdp or len(sdp) > 256_000:
                        await websocket.close(code=4400, reason="Invalid WebRTC session description")
                        break
                if kind == "call.ice":
                    candidate = message.get("candidate")
                    if candidate is not None and (not isinstance(candidate, dict) or len(json.dumps(candidate)) > 8192):
                        await websocket.close(code=4400, reason="Invalid ICE candidate")
                        break
                if kind == "call.connected":
                    call.status = "connected"
                relay = dict(message)
                relay["from_device_id"] = device_id
                relay["from_role"] = role
                await peer_socket.send_json(relay)
                continue
            await websocket.close(code=4400, reason="Unsupported connection message")
            break
    except WebSocketDisconnect:
        pass
    except (ValueError, json.JSONDecodeError):
        await websocket.close(code=4400, reason="Invalid message")
    finally:
        if store.active_sockets.get(device_id) is websocket:
            store.active_sockets.pop(device_id, None)
        for call_id, call in list(store.controlled_calls.items()):
            if device_id in {call.far_device_id, call.near_device_id}:
                other_id = call.peer_for(device_id)
                other = store.active_sockets.get(other_id or "")
                if other:
                    try:
                        await other.send_json({"type": "call.ended", "protocol_version": 1, "call_id": call_id, "reason": "The peer desktop connection closed."})
                    except (RuntimeError, WebSocketDisconnect):
                        pass
                store.controlled_calls.pop(call_id, None)
                for key, session in list(store.audio_stream_sessions.items()):
                    if session.call_id == call_id:
                        session.status = "disconnected"
                        session.ended_at = now()
                        stream_socket = store.audio_stream_sockets.pop(key, None)
                        if stream_socket:
                            try:
                                await stream_socket.close(code=1000, reason="Call signaling disconnected")
                            except (RuntimeError, WebSocketDisconnect):
                                pass


@router.websocket("/api/v1/mobile/audio")
async def mobile_audio_socket(websocket: WebSocket):
    if websocket.scope.get("scheme") != "wss":
        await websocket.close(code=4400, reason="Encrypted WebSocket is required")
        return
    device_id = websocket.headers.get("x-device-id", "")
    token = websocket.headers.get("authorization", "").removeprefix("Bearer ")
    store: DesktopStore = websocket.app.state.store
    if not device_id or not token or not store.authenticate(device_id, token):
        await websocket.close(code=4401, reason="Device authentication failed")
        return
    devices = {item["device_id"]: item for item in store.list_devices()}
    device = devices.get(device_id)
    if not device or device["revoked"]:
        await websocket.close(code=4403, reason="Pairing revoked")
        return
    await websocket.accept()
    session: AudioSession | None = None
    session_key: str | None = None
    await websocket.send_json({"type": "audio.ready", "protocol_version": 1})
    try:
        while True:
            message = await websocket.receive()
            if message.get("type") == "websocket.disconnect":
                break
            text_message = message.get("text")
            binary_message = message.get("bytes")
            if text_message is not None:
                try:
                    control = json.loads(text_message)
                except (TypeError, json.JSONDecodeError):
                    await websocket.close(code=4400, reason="Invalid audio control message")
                    break
                if not isinstance(control, dict) or control.get("protocol_version") != 1:
                    await websocket.close(code=4400, reason="Unsupported audio protocol version")
                    break
                kind = control.get("type")
                if kind == "monitor.start":
                    mode = control.get("mode")
                    if mode not in {"cellular_protection", "audio_transport_test", "file_replay", "controlled_webrtc"}:
                        await websocket.close(code=4400, reason="Unsupported audio source mode")
                        break
                    if mode == "controlled_webrtc":
                        call_id = control.get("call_id")
                        stream_id = control.get("stream_id")
                        track_id = control.get("track_id")
                        call = store.controlled_calls.get(call_id) if isinstance(call_id, str) else None
                        if (
                            not call or call.status not in {"active", "connected"}
                            or device_id != call.near_device_id
                            or track_id not in {"near", "far"}
                            or not isinstance(stream_id, str)
                            or not stream_id or len(stream_id) > 64
                        ):
                            await websocket.close(code=4403, reason="Audio stream is not authorized for this call participant")
                            break
                        session_key = f"{device_id}:{call.call_id}:{stream_id}"
                        if session_key in store.audio_stream_sessions:
                            await websocket.close(code=4409, reason="Audio stream ID is already in use")
                            break
                        if any(
                            item.call_id == call.call_id and item.device_id == device_id
                            and item.role == track_id and item.status in {"armed", "receiving"}
                            for item in store.audio_stream_sessions.values()
                        ):
                            await websocket.close(code=4409, reason="This call already has an active stream for that audio role")
                            break
                        session = AudioSession(
                            device_id=device_id, device_name=device["device_name"],
                            source="controlled_webrtc", status="armed", stream_key=session_key,
                            call_id=call.call_id, role=track_id,
                        )
                        store.audio_stream_sessions[session_key] = session
                        store.audio_stream_sockets[session_key] = websocket
                    else:
                        previous = store.audio_sockets.get(device_id)
                        if previous and previous is not websocket:
                            try:
                                await previous.close(code=4409, reason="A newer audio connection replaced this session")
                            except (RuntimeError, WebSocketDisconnect):
                                pass
                        store.audio_sockets[device_id] = websocket
                        session_key = device_id
                        if device_id not in store.audio_sessions and len(store.audio_sessions) + len(store.webrtc_sessions) + len(store.audio_stream_sessions) >= MAX_AUDIO_MONITORS:
                            inactive = next((
                                key for key, value in store.audio_sessions.items()
                                if value.status in {"stopped", "disconnected", "revoked"}
                            ), None)
                            if inactive is None:
                                await websocket.close(code=4429, reason="Desktop audio monitor capacity is full")
                                break
                            store.audio_sessions.pop(inactive, None)
                        session = AudioSession(device_id=device_id, device_name=device["device_name"])
                        store.audio_sessions[device_id] = session
                    if len(store.audio_sessions) + len(store.webrtc_sessions) + len(store.audio_stream_sessions) > MAX_AUDIO_MONITORS:
                        if session_key and session_key in store.audio_stream_sessions:
                            store.audio_stream_sessions.pop(session_key, None)
                            store.audio_stream_sockets.pop(session_key, None)
                        await websocket.close(code=4429, reason="Desktop audio monitor capacity is full")
                        break
                    await websocket.send_json({"type": "monitor.ready", "protocol_version": 1, "stream_id": control.get("stream_id")})
                elif kind == "audio.start":
                    if session is None or session.status != "armed":
                        await websocket.close(code=4400, reason="Audio monitor must be armed first")
                        break
                    source = control.get("source")
                    if (
                        source not in {"cellular_microphone", "diagnostic_tone", "file_replay", "controlled_webrtc"}
                        or (session.source == "controlled_webrtc" and source != "controlled_webrtc")
                        or (session.source != "controlled_webrtc" and source == "controlled_webrtc")
                        or control.get("sample_rate") != 16000
                        or control.get("channels") != 1
                        or control.get("sample_format") != "pcm_s16le"
                        or control.get("frame_duration_ms") != 20
                        or control.get("frame_bytes") != 640
                        or not isinstance(control.get("session_id"), str)
                        or len(control["session_id"]) > 64
                    ):
                        await websocket.close(code=4400, reason="Unsupported audio format")
                        break
                    session.session_id = control["session_id"]
                    session.source = source
                    session.status = "receiving"
                    session.sample_rate = control["sample_rate"]
                    session.channels = control["channels"]
                    session.sample_format = control["sample_format"]
                    session.frame_duration_ms = control["frame_duration_ms"]
                    session.frame_bytes = control["frame_bytes"]
                    session.frames_received = 0
                    session.bytes_received = 0
                    session.dropped_frames = 0
                    session.sequence_gaps = 0
                    session.last_sequence = -1
                    session.last_capture_ns = 0
                    session.last_rms = 0.0
                    session.last_peak = 0
                    session.jitter_ewma_ms = 0.0
                    session.round_trip_ms = None
                    session.estimated_one_way_ms = None
                    session.previous_arrival_ns = None
                    session.started_at = now()
                    session.ended_at = None
                    session.preview_frames.clear()
                    await websocket.send_json({
                        "type": "audio.accepted",
                        "session_id": session.session_id,
                        "desktop_monotonic_ns": time.monotonic_ns(),
                    })
                elif kind == "audio.stop":
                    if session and control.get("session_id") == session.session_id:
                        session.status = "armed"
                        session.ended_at = now()
                        session.session_id = None
                elif kind == "monitor.stop":
                    if session:
                        session.status = "stopped"
                        session.ended_at = now()
                    await websocket.send_json({"type": "monitor.stopped"})
                    break
                elif kind == "audio.probe":
                    if session is None:
                        await websocket.close(code=4400, reason="Audio monitor must be armed before latency probes")
                        break
                    probe_id = control.get("probe_id")
                    phone_elapsed_ns = control.get("phone_elapsed_ns")
                    if not isinstance(probe_id, str) or len(probe_id) > 64 or not isinstance(phone_elapsed_ns, int):
                        await websocket.close(code=4400, reason="Invalid latency probe")
                        break
                    await websocket.send_json({
                        "type": "audio.probe_ack",
                        "probe_id": probe_id,
                        "phone_elapsed_ns": phone_elapsed_ns,
                        "desktop_monotonic_ns": time.monotonic_ns(),
                    })
                elif kind == "audio.metrics":
                    if session:
                        session.dropped_frames = max(0, min(1000000, int(control.get("dropped_frames", session.dropped_frames))))
                        rtt = control.get("round_trip_ms")
                        one_way = control.get("estimated_one_way_ms")
                        if isinstance(rtt, (int, float)) and 0 <= rtt <= 60000:
                            session.round_trip_ms = float(rtt)
                        if isinstance(one_way, (int, float)) and 0 <= one_way <= 30000:
                            session.estimated_one_way_ms = float(one_way)
                else:
                    await websocket.close(code=4400, reason="Unsupported audio control message")
                    break
            elif binary_message is not None:
                if session is None or session.status != "receiving":
                    await websocket.close(code=4400, reason="Audio frame received without an active session")
                    break
                if len(binary_message) != 656 or binary_message[:4] != b"VDA1":
                    await websocket.close(code=4400, reason="Invalid audio frame size or header")
                    break
                sequence, capture_ns = struct.unpack(">IQ", binary_message[4:16])
                pcm = binary_message[16:]
                if capture_ns <= session.last_capture_ns or sequence <= session.last_sequence:
                    await websocket.close(code=4400, reason="Audio frame sequence or timing is invalid")
                    break
                if session.last_sequence >= 0 and sequence > session.last_sequence + 1:
                    session.sequence_gaps += sequence - session.last_sequence - 1
                store.ingest_pcm_frame(session, pcm, sequence, capture_ns)
                if time.monotonic_ns() - session.last_store_touch_ns >= 5_000_000_000:
                    store.touch(device_id)
                    session.last_store_touch_ns = time.monotonic_ns()
            else:
                await websocket.close(code=4400, reason="Empty audio WebSocket message")
                break
    except WebSocketDisconnect:
        pass
    except (ValueError, TypeError, struct.error, OverflowError):
        await websocket.close(code=4400, reason="Invalid audio message")
    finally:
        if store.audio_sockets.get(device_id) is websocket:
            store.audio_sockets.pop(device_id, None)
        if session_key and store.audio_stream_sockets.get(session_key) is websocket:
            store.audio_stream_sockets.pop(session_key, None)
        stream_session = store.audio_stream_sessions.get(session_key or "")
        if session and stream_session is session and session.status not in {"revoked", "stopped"}:
            session.status = "disconnected"
            session.ended_at = now()
        elif session and store.audio_sessions.get(device_id) is session and session.status != "revoked":
            if session.status == "receiving":
                session.status = "disconnected"
            elif session.status == "armed":
                session.status = "disconnected"
            session.ended_at = now()


@router.get("/api/v1/desktop/audio/{device_id}/preview.wav")
def audio_preview(request: Request, device_id: str):
    require_loopback(request)
    store = get_store(request)
    session = store.audio_sessions.get(device_id) or store.webrtc_sessions.get(device_id) or store.audio_stream_sessions.get(device_id)
    if session is None:
        session = next((item for item in (*store.webrtc_sessions.values(), *store.audio_stream_sessions.values()) if item.stream_key == device_id or item.device_id == device_id), None)
    if not session or not session.preview_frames:
        raise HTTPException(status_code=404, detail="No recent audio is available for playback.")
    pcm = b"".join(session.preview_frames)
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(pcm)
    return Response(
        output.getvalue(),
        media_type="audio/wav",
        headers={"Cache-Control": "no-store", "Content-Disposition": "inline; filename=recent-audio-preview.wav"},
    )


def _authenticated_mobile(request: Request) -> tuple[DesktopStore, str, dict[str, Any]]:
    require_https(request)
    store = get_store(request)
    device_id = request.headers.get("x-device-id", "")
    token = request.headers.get("authorization", "").removeprefix("Bearer ")
    if not device_id or not token or not store.authenticate(device_id, token):
        raise HTTPException(status_code=403, detail="This phone is not paired or its pairing was revoked.")
    device = next((item for item in store.list_devices() if item["device_id"] == device_id), None)
    if not device or device["revoked"]:
        raise HTTPException(status_code=403, detail="This phone pairing was revoked.")
    return store, device_id, device


def _decode_voice_message(raw: bytes) -> bytes:
    import av

    container = av.open(io.BytesIO(raw))
    try:
        audio_stream = next((stream for stream in container.streams if stream.type == "audio"), None)
        if audio_stream is None:
            raise ValueError("No audio track was found.")
        resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
        pcm = bytearray()
        for decoded in container.decode(audio_stream):
            for converted in resampler.resample(decoded):
                pcm.extend(converted.to_ndarray().tobytes())
                if len(pcm) > MAX_VOICE_MESSAGE_SECONDS * 16000 * 2:
                    raise ValueError("Voice message exceeds the 10-minute limit.")
        for converted in resampler.resample(None):
            pcm.extend(converted.to_ndarray().tobytes())
        if len(pcm) > MAX_VOICE_MESSAGE_SECONDS * 16000 * 2:
            raise ValueError("Voice message exceeds the 10-minute limit.")
        return bytes(pcm)
    finally:
        container.close()


@router.post("/api/v1/mobile/voice-messages")
async def upload_voice_message(
    request: Request,
    audio: UploadFile = File(...),
    call_id: str = Form(...),
):
    store, device_id, device = _authenticated_mobile(request)
    if not call_id or len(call_id) > 64 or not all(ch.isalnum() or ch in "-_" for ch in call_id):
        raise HTTPException(status_code=400, detail="Invalid voice-message call ID.")
    raw = await audio.read(MAX_VOICE_MESSAGE_BYTES + 1)
    if not raw or len(raw) > MAX_VOICE_MESSAGE_BYTES:
        raise HTTPException(status_code=413, detail="Voice message must be between 1 byte and 25 MB.")
    try:
        pcm = await asyncio.to_thread(_decode_voice_message, raw)
    except Exception as error:
        raise HTTPException(status_code=400, detail=f"Audio could not be decoded: {error}") from error
    if not pcm:
        raise HTTPException(status_code=400, detail="Voice message contains no decodable audio.")
    file_name = f"{uuid.uuid4().hex}.wav"
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(pcm)
    target = store.voice_message_dir / file_name
    file_bytes = output.getvalue()
    async with store.voice_message_lock:
        stored_bytes = sum(int(item["file_size"]) for item in store.list_voice_messages())
        if stored_bytes + len(file_bytes) > MAX_STORED_VOICE_MESSAGE_BYTES:
            raise HTTPException(status_code=507, detail="Desktop voice-message storage is full. Delete older clips and try again.")
        await asyncio.to_thread(target.write_bytes, file_bytes)
        created_at = iso(now()) or ""
        duration = len(pcm) / (16000 * 2)
        try:
            store.add_voice_message(device_id, device["device_name"], call_id, file_name, created_at, duration, len(file_bytes))
        except Exception:
            target.unlink(missing_ok=True)
            raise
    store.touch(device_id)
    return {"status": "stored", "call_id": call_id, "duration_seconds": round(duration, 2), "retention_hours": 24}


@router.get("/api/v1/desktop/voice-messages/{message_id}.wav")
def get_voice_message(request: Request, message_id: str):
    require_loopback(request)
    store = get_store(request)
    record = next((item for item in store.list_voice_messages() if item["message_id"] == message_id), None)
    if not record:
        raise HTTPException(status_code=404, detail="Voice message is no longer available.")
    path = store.voice_message_dir / record["file_name"]
    if not path.is_file():
        raise HTTPException(status_code=404, detail="Voice message file is no longer available.")
    return Response(path.read_bytes(), media_type="audio/wav", headers={"Cache-Control": "no-store", "Content-Disposition": "inline; filename=voice-message.wav"})


@router.delete("/api/v1/desktop/voice-messages/{message_id}")
def delete_voice_message(request: Request, message_id: str):
    require_loopback(request)
    if not get_store(request).delete_voice_message(message_id):
        raise HTTPException(status_code=404, detail="Voice message was not found.")
    return {"status": "deleted"}


@router.websocket("/api/v1/mobile/webrtc")
async def mobile_webrtc_socket(websocket: WebSocket):
    if websocket.scope.get("scheme") != "wss":
        await websocket.close(code=4400, reason="Encrypted WebSocket is required")
        return
    device_id = websocket.headers.get("x-device-id", "")
    token = websocket.headers.get("authorization", "").removeprefix("Bearer ")
    store: DesktopStore = websocket.app.state.store
    if not device_id or not token or not store.authenticate(device_id, token):
        await websocket.close(code=4401, reason="Device authentication failed")
        return
    device = next((item for item in store.list_devices() if item["device_id"] == device_id), None)
    if not device or device["revoked"]:
        await websocket.close(code=4403, reason="Pairing revoked")
        return
    await websocket.accept()
    peer = None
    session = None
    stream_key = None
    consumer_tasks: set[asyncio.Task] = set()
    try:
        from aiortc import RTCPeerConnection, RTCSessionDescription
        import av

        request_message = await websocket.receive_json()
        if not isinstance(request_message, dict) or request_message.get("type") != "webrtc.offer" or request_message.get("protocol_version") != 1:
            await websocket.close(code=4400, reason="Expected a version 1 WebRTC offer")
            return
        call_id = request_message.get("call_id")
        role = request_message.get("role")
        offer_sdp = request_message.get("sdp")
        if not isinstance(call_id, str) or not call_id or len(call_id) > 64 or not all(ch.isalnum() or ch in "-_" for ch in call_id):
            await websocket.close(code=4400, reason="Invalid call ID")
            return
        if role not in {"near", "far"} or not isinstance(offer_sdp, str) or len(offer_sdp) > 256_000:
            await websocket.close(code=4400, reason="Invalid WebRTC role or offer")
            return
        active_streams = sum(session.status in {"armed", "receiving"} for session in store.webrtc_sessions.values())
        active_streams += sum(session.status in {"armed", "receiving"} for session in store.audio_sessions.values())
        active_streams += sum(session.status in {"armed", "receiving"} for session in store.audio_stream_sessions.values())
        if active_streams >= MAX_AUDIO_MONITORS:
            await websocket.close(code=4429, reason="Desktop audio monitor capacity is full")
            return
        while len(store.webrtc_sessions) + len(store.audio_sessions) + len(store.audio_stream_sessions) >= MAX_AUDIO_MONITORS:
            stale = next((key for key, item in store.webrtc_sessions.items() if item.status in {"stopped", "disconnected", "revoked"}), None)
            if stale is not None:
                store.webrtc_sessions.pop(stale, None)
                continue
            stale_audio = next((key for key, item in store.audio_sessions.items() if item.status in {"stopped", "disconnected", "revoked"}), None)
            if stale_audio is not None:
                store.audio_sessions.pop(stale_audio, None)
                continue
            stale_controlled = next((key for key, item in store.audio_stream_sessions.items() if item.status in {"stopped", "disconnected", "revoked"}), None)
            if stale_controlled is not None:
                store.audio_stream_sessions.pop(stale_controlled, None)
                continue
            await websocket.close(code=4429, reason="Desktop audio monitor capacity is full")
            return
        stream_key = uuid.uuid4().hex
        peer = RTCPeerConnection()
        session = AudioSession(device_id=device_id, device_name=device["device_name"], source=f"app_webrtc_{role}", status="armed", stream_key=stream_key, call_id=call_id, role=role)
        store.webrtc_sessions[stream_key] = session
        store.webrtc_sockets[f"{device_id}:{stream_key}"] = websocket

        @peer.on("track")
        def on_track(track):
            if track.kind != "audio":
                return

            async def receive_track():
                resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
                pending = bytearray()
                sequence = 0
                session.status = "receiving"
                while True:
                    frame = await track.recv()
                    for converted in resampler.resample(frame):
                        pending.extend(converted.to_ndarray().tobytes())
                        while len(pending) >= PCM_FRAME_BYTES:
                            pcm = bytes(pending[:PCM_FRAME_BYTES])
                            del pending[:PCM_FRAME_BYTES]
                            store.ingest_pcm_frame(session, pcm, sequence, time.monotonic_ns())
                            sequence += 1

            task = asyncio.create_task(receive_track())
            consumer_tasks.add(task)
            def finish_consumer(completed: asyncio.Task) -> None:
                consumer_tasks.discard(completed)
                if completed.cancelled():
                    return
                error = completed.exception()
                if error and session.status == "receiving":
                    session.status = "disconnected"
                    session.ended_at = now()

            task.add_done_callback(finish_consumer)

        await peer.setRemoteDescription(RTCSessionDescription(sdp=offer_sdp, type="offer"))
        answer = await peer.createAnswer()
        await peer.setLocalDescription(answer)
        await websocket.send_json({"type": "webrtc.answer", "protocol_version": 1, "call_id": call_id, "role": role, "sdp": peer.localDescription.sdp, "sdp_type": peer.localDescription.type})
        store.touch(device_id)
        while True:
            control = await websocket.receive_json()
            if not isinstance(control, dict) or control.get("protocol_version") != 1:
                await websocket.close(code=4400, reason="Invalid WebRTC control message")
                break
            if control.get("type") == "webrtc.stop" and control.get("call_id") == call_id:
                break
            await websocket.close(code=4400, reason="Unsupported WebRTC control message")
            break
    except WebSocketDisconnect:
        pass
    except Exception as error:
        try:
            await websocket.send_json({"type": "webrtc.error", "detail": str(error)[:240]})
        except (RuntimeError, WebSocketDisconnect):
            pass
    finally:
        for task in consumer_tasks:
            task.cancel()
        if consumer_tasks:
            await asyncio.gather(*consumer_tasks, return_exceptions=True)
        if peer is not None:
            await peer.close()
        if stream_key:
            store.webrtc_sockets.pop(f"{device_id}:{stream_key}", None)
        if session and session.status not in {"revoked", "stopped"}:
            session.status = "stopped" if websocket.client_state.name == "CONNECTED" else "disconnected"
            session.ended_at = now()
