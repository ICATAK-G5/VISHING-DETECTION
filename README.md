# AI Voice Phishing Detection

This repository is the implementation workspace for the project architecture migrated from the ChatGPT conversation **Cybersecurity Project Analysis**. The current source of truth is [the architecture brief](docs/PROJECT_ARCHITECTURE.md) and [the implementation roadmap](docs/ROADMAP.md).

## Project objective

Build a privacy-aware prototype that analyzes suspicious voice conversations, explains its risk findings, alerts the user, and can produce a user-reviewed evidence package. Analysis runs on the desktop. The phone provides the user interface and, where the platform permits, an explicitly enabled audio path.

## Frozen architectural decisions

- Two modes: **Cellular Protection** and **Controlled VoIP/SIP**.
- Cellular mode does not assume general third-party access to cellular call audio. The initial demonstration uses supported call metadata and user-assisted speakerphone capture where available.
- Controlled VoIP/SIP is the end-to-end demonstration path because the project controls both audio channels and call routing.
- Flutter/Dart is the shared mobile UI; Kotlin and Swift platform modules provide native integrations where supported.
- Python desktop services host audio processing and analysis. Authenticated WebSocket carries control/signaling and the existing VDA1 diagnostic/capture streams; WebRTC carries controlled digital audio media.
- Specialized analysis signals are combined by a deterministic fusion/risk engine. An LLM may explain and summarize findings but does not control safety-critical actions.
- Local processing and explicit user consent are defaults. Incident evidence is retained only through a deliberate save/report workflow; authority submission requires user review and authorization.
- Implement one feature at a time, following the agreed phase sequence in `docs/ROADMAP.md`.

## Start here

Phase 1 (development environment) and Phase 2 (mobile-to-desktop connection) are complete. Phase 3 implementation is in progress and includes the separate OS-limited Cellular Protection mode, direct phone-to-desktop WebRTC audio, a consented Phone A ↔ Phone B in-app WebRTC call with desktop-issued call ID and near/far roles, separate VDA1 streams from the target phone to the dashboard, recorded voice messages, and paced file replay. Cellular Protection keeps the microphone off while Android reports idle or ringing and starts on off-hook; Android does not grant ordinary apps direct SIM-call audio access. Phase 4 covers VAD; Phase 5 covers speech recognition. Rule/risk engines, LLM analysis, fusion, warnings, reports, privacy controls, voice/caller analysis, controlled VoIP/SIP, honeypot, and full integration follow in their listed phases.

## Collaboration and delivery workflow

- Use ChatGPT (Work) to discuss and settle the details of the current roadmap phase.
- Do not begin implementation until the discussion is complete and the user confirms that the phase is ready to build.
- After confirmation, prepare a focused implementation prompt for Codex; use Codex for the coding work.
- Phase numbering follows `docs/ROADMAP.md`, including Phase 1 completion in the earlier project chat and Phase 2 connection work completed before Phase 3.
- Both ChatGPT (Work) and Codex use this shared local workspace. Commit the local project files to the user's GitHub repository at regular checkpoints; review changes before committing.
- Work through the roadmap in order, one phase at a time, and carry forward unresolved decisions explicitly rather than silently assuming them.
