# Project Structure

This document defines the repository layout and ownership boundaries. It follows the architecture in [PROJECT_ARCHITECTURE.md](PROJECT_ARCHITECTURE.md) and the sequence in [ROADMAP.md](ROADMAP.md). A folder boundary does not imply that its feature has been implemented.

```text
VISHING DETECTION/
├── backend/
│   ├── .venv/                 # Local Python environment; ignored by Git
│   ├── src/vishing/
│   │   ├── api/               # HTTP/WebSocket boundary (Phase 4+)
│   │   ├── core/              # App startup and configuration (Phase 4+)
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
├── mobile_app/                # Flutter client; app scaffold in Phase 5
├── docs/                      # Architecture, requirements, roadmap, decisions
├── .gitignore
└── README.md
```

## Responsibilities

- **Desktop service:** Python owns audio processing, analysis, and risk reporting. FastAPI will serve the local browser dashboard and service API when Phase 4 begins. Until authenticated encrypted transport is implemented, it must bind to loopback only.
- **Mobile client:** Flutter owns the phone UI, consent, and visible status. It is a client; analysis remains on the desktop. Native Android/iOS integrations are limited by each platform's supported APIs.
- **Operating modes:** Keep Cellular Protection and Controlled VoIP/SIP as separate boundaries. Do not claim general cellular-call audio access. The controlled VoIP/SIP path is the full-audio demonstration path.
- **Sensitive material:** Do not commit secrets, private call recordings, or generated model weights. Keep user data local and user-controlled.

## Build sequence

1. **Repository and development environment:** establish the folder boundaries, dependency environment, and contributor setup.
2. **Desktop service skeleton:** add the FastAPI application, loopback configuration, health route, and browser dashboard.
3. **Mobile app skeleton:** generate the Flutter Android project and implement the initial consent/status shell.
4. Add authenticated encrypted phone-to-desktop connectivity only in the transport phase; implement audio and analysis in their later roadmap phases.

The current Phase 1 baseline has the documentation and dependency environment. The package and app directories below are placeholders only; executable backend and mobile applications belong to their later roadmap phases.
