"""Replay a local audio clip to the paired desktop as 50 Hz VDA1 frames.

Credentials are read from environment variables so they are not placed in the
script or its command-line arguments. This is a transport diagnostic, not an
analysis command.
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import os
import ssl
import struct
import time
from pathlib import Path

import av
import websockets

FRAME_SAMPLES = 320
FRAME_BYTES = FRAME_SAMPLES * 2
FRAME_SECONDS = 0.020


def load_pcm(path: Path) -> bytes:
    container = av.open(str(path))
    stream = next((item for item in container.streams if item.type == "audio"), None)
    if stream is None:
        raise ValueError("Input file contains no audio stream")
    resampler = av.AudioResampler(format="s16", layout="mono", rate=16000)
    pcm = bytearray()
    for frame in container.decode(stream):
        for converted in resampler.resample(frame):
            pcm.extend(converted.to_ndarray().tobytes())
    for converted in resampler.resample(None):
        pcm.extend(converted.to_ndarray().tobytes())
    container.close()
    if not pcm:
        raise ValueError("Input file decoded to no audio samples")
    return bytes(pcm)


async def replay(path: Path) -> None:
    required = ("VDA_DEVICE_ID", "VDA_DEVICE_TOKEN", "VDA_TLS_FINGERPRINT", "VDA_WSS_URL", "VDA_CERT_FILE")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise ValueError("Set these environment variables before starting: " + ", ".join(missing))
    pcm = load_pcm(path)
    device_id = os.environ["VDA_DEVICE_ID"]
    call_id = f"file_{int(time.time())}"
    url = os.environ["VDA_WSS_URL"].replace("/api/v1/mobile/ws", "/api/v1/mobile/audio")
    fingerprint = os.environ["VDA_TLS_FINGERPRINT"].lower().replace(":", "").strip()
    context = ssl.create_default_context(cafile=os.environ["VDA_CERT_FILE"])
    context.check_hostname = False

    async with websockets.connect(
        url,
        additional_headers={"Authorization": f"Bearer {os.environ['VDA_DEVICE_TOKEN']}", "X-Device-ID": device_id},
        ssl=context,
        max_size=262144,
    ) as socket:
        ssl_socket = socket.transport.get_extra_info("ssl_object")
        certificate = ssl_socket.getpeercert(binary_form=True) if ssl_socket else None
        if not certificate or hashlib.sha256(certificate).hexdigest() != fingerprint:
            raise ssl.SSLError("Desktop certificate fingerprint does not match the saved pairing pin")

        await socket.send(json.dumps({"type": "monitor.start", "protocol_version": 1, "mode": "file_replay"}))
        response = json.loads(await socket.recv())
        if response.get("type") != "monitor.ready":
            raise RuntimeError(f"Desktop did not arm the audio monitor: {response}")
        await socket.send(json.dumps({
            "type": "audio.start", "protocol_version": 1, "source": "file_replay",
            "sample_rate": 16000, "channels": 1, "sample_format": "pcm_s16le",
            "frame_duration_ms": 20, "frame_bytes": FRAME_BYTES, "session_id": call_id,
        }))
        response = json.loads(await socket.recv())
        if response.get("type") != "audio.accepted":
            raise RuntimeError(f"Desktop rejected the audio format: {response}")

        frame_count = (len(pcm) + FRAME_BYTES - 1) // FRAME_BYTES
        timeline = time.monotonic()
        for sequence in range(frame_count):
            start = sequence * FRAME_BYTES
            payload = pcm[start:start + FRAME_BYTES]
            if len(payload) < FRAME_BYTES:
                payload += bytes(FRAME_BYTES - len(payload))
            packet = b"VDA1" + struct.pack(">IQ", sequence, time.monotonic_ns()) + payload
            await socket.send(packet)
            target = timeline + (sequence + 1) * FRAME_SECONDS
            delay = target - time.monotonic()
            if delay > 0:
                await asyncio.sleep(delay)
        await socket.send(json.dumps({"type": "audio.stop", "protocol_version": 1, "session_id": call_id}))
        await socket.send(json.dumps({"type": "monitor.stop", "protocol_version": 1}))
        print(f"Replayed {frame_count} frames ({frame_count * FRAME_SECONDS:.1f} seconds) to the paired desktop.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("audio_file", type=Path, help="Local audio clip to normalize and replay")
    args = parser.parse_args()
    if not args.audio_file.is_file():
        parser.error("audio_file does not exist")
    asyncio.run(replay(args.audio_file))


if __name__ == "__main__":
    main()
