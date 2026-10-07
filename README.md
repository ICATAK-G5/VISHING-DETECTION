# AI Voice Phishing Detection

This repository is the implementation workspace for the project architecture migrated from the ChatGPT conversation **Cybersecurity Project Analysis**. The current source of truth is [the architecture brief](docs/PROJECT_ARCHITECTURE.md) and [the implementation roadmap](docs/ROADMAP.md).

## Project objective

Build a privacy-aware prototype that analyzes suspicious voice conversations, explains its risk findings, alerts the user, and can produce a user-reviewed evidence package. Analysis runs on the desktop. The phone provides the user interface and, where the platform permits, an explicitly enabled audio path.

## Frozen architectural decisions

- Two modes: **Cellular Protection** and **Controlled VoIP/SIP**.
- Cellular mode does not assume general third-party access to cellular call audio. The initial demonstration uses supported call metadata and user-assisted speakerphone capture where available.
- Controlled VoIP/SIP is the end-to-end demonstration path because the project controls both audio channels and call routing.
- Flutter/Dart is the shared mobile UI; Kotlin and Swift platform modules provide native integrations where supported.
- Python desktop services host audio processing and analysis. WebSocket is the first streaming transport; WebRTC is a later option.
- Specialized analysis signals are combined by a deterministic fusion/risk engine. An LLM may explain and summarize findings but does not control safety-critical actions.
- Local processing and explicit user consent are defaults. Incident evidence is retained only through a deliberate save/report workflow; authority submission requires user review and authorization.
- Implement one feature at a time, following the agreed phase sequence in `docs/ROADMAP.md`.

## Start here

Phase 1 (development environment) is complete. Phase 2 is in progress and adds the Flutter connection client, local Python/FastAPI receiving service, and browser dashboard. Audio capture and analysis remain later phases.

## Collaboration and delivery workflow

- Use ChatGPT (Work) to discuss and settle the details of the current roadmap phase.
- Do not begin implementation until the discussion is complete and the user confirms that the phase is ready to build.
- After confirmation, prepare a focused implementation prompt for Codex; use Codex for the coding work.
- Phase numbering follows `docs/ROADMAP.md`, including Phase 1 completion in the earlier project chat and the current Phase 2 connection work.
- Both ChatGPT (Work) and Codex use this shared local workspace. Commit the local project files to the user's GitHub repository at regular checkpoints; review changes before committing.
- Work through the roadmap in order, one phase at a time, and carry forward unresolved decisions explicitly rather than silently assuming them.
