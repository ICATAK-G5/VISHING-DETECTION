# Project Structure

This document defines the repository layout and ownership boundaries. It follows the architecture in [PROJECT_ARCHITECTURE.md](PROJECT_ARCHITECTURE.md) and the sequence in [ROADMAP.md](ROADMAP.md). A folder boundary does not imply that its feature has been implemented.

```text
VISHING DETECTION/
├── backend/
│   ├── .venv/                 # Local Python environment; ignored by Git
│   ├── src/vishing/
│   │   ├── api/               # HTTP/WebSocket pairing, connection, and audio transport
│   │   ├── core/              # Service configuration, TLS, and credential hashing
│   │   ├── dashboard/         # Local browser control surface
│   │   ├── modes/
│   │   │   ├── cellular/      # Platform-constrained protection path
│   │   │   └── voip/          # Controlled VoIP/SIP demonstration path
│   │   ├── audio/             # Audio input and preprocessing
│   │   ├── analysis/          # Speech and phishing-signal processing
│   │   ├── risk/              # Evidence-based risk fusion
│   │   ├── incidents/         # User-controlled incident handling
│   │   └── privacy/           # Consent, retention, and redaction rules
│   ├── requirements.txt       # Python dependency pins
│   └── README.md
├── mobile_app/
│   ├── lib/                    # Flutter pairing, direct audio, controlled calls, clips, and consent UI
│   └── android/app/src/main/kotlin/ # Foreground services, audio capture, and WebRTC PCM-to-VDA1 tap
├── scripts/
│   └── replay_audio.py         # Phone X diagnostic: paced 50 fps VDA1 replay
├── docs/                      # Architecture, requirements, roadmap, decisions
├── .gitignore
└── README.md
```

## Responsibilities

- **Desktop service:** Python/FastAPI owns the receiving API and, in later phases, audio processing, analysis, and risk reporting. The local browser dashboard manages pairing and device status. The dashboard and management API remain loopback-only; the TLS phone listener is loopback-only by default and can be explicitly enabled on a private interface after authentication and encryption are present.
- **Mobile client:** Flutter owns the phone UI, consent, and visible status. It is a client; analysis remains on the desktop. Native Android/iOS integrations are limited by each platform's supported APIs.
- **Operating modes:** Keep Cellular Protection and Controlled VoIP/SIP as separate boundaries. Do not claim general cellular-call audio access. The controlled VoIP/SIP path is the full-audio demonstration path.
- **Sensitive material:** Do not commit secrets, private call recordings, or generated model weights. Keep user data local and user-controlled.

## Current implementation status

Phase 1 and Phase 2 are complete. Phase 3 implementation includes the OS-limited cellular-protection microphone/WSS path, direct phone-to-desktop WebRTC, controlled Phone A ↔ Phone B WebRTC calling with two target-phone VDA1 streams, consented data-channel voice-message transfer and desktop upload, 60-second live RAM previews, and paced VDA1 file replay. VAD, transcription, and analysis remain later roadmap work. Voice-message files live outside the repository under the user's local application-data directory and expire after 24 hours. The Phone A ↔ Phone B, received-clip, and file-replay flows still require two-physical-device validation.

See [ROADMAP.md](ROADMAP.md) for the agreed phase sequence and Phase 2 acceptance criteria.
