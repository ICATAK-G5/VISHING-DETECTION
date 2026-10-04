# Implementation Roadmap

This 17-phase workflow is the current project board baseline. It replaces the older 14-phase workflow. Work feature by feature; start the next feature when the current phase is complete or a dependency requires parallel preparation. Define acceptance criteria and evidence for each phase before implementation.

| Phase | Work | Completion outcome |
|---:|---|---|
| 1 | Project scope, architecture, constraints | Team agrees on the two modes, responsibilities, limitations, and prototype boundary. |
| 2 | Requirements and acceptance criteria | Functional, privacy, security, performance, and device-capability requirements are written. |
| 3 | Repository and development environment | Folder layout, Python environment, Flutter setup, version control, and contributor instructions are ready. |
| 4 | Desktop service skeleton | Local FastAPI app starts with health/status endpoints and configuration. |
| 5 | Mobile app skeleton | Flutter app runs on a selected Android test device; iOS scaffold/platform boundary is documented. |
| 6 | Secure phone-to-desktop connection | Authenticated local HTTPS/WSS connection and connection status are demonstrated. |
| 7 | Audio input and consent UX | Explicit consent, visible active state, supported microphone/speakerphone input, and stop control work. |
| 8 | Audio streaming gateway | WebSocket audio chunks arrive with session metadata and bounded buffering. |
| 9 | Audio preprocessing and VAD | Audio validation/preprocessing and Silero VAD candidate produce speech segments. |
| 10 | Speech-to-text | faster-whisper Base produces timestamped transcript; benchmark Small if resources allow. |
| 11 | Transcript and live status UI | Transcript/status updates reach desktop and phone with clear failure/quality states. |
| 12 | Rule-based phishing indicators | Credential, financial, urgency, impersonation, secrecy, and remote-access indicators produce traceable findings. |
| 13 | Contextual model analysis | Local LLM returns schema-validated findings and explanations; untrusted input boundaries are enforced. |
| 14 | Fusion and risk reporting | Deterministic engine combines available signals and produces calibrated levels and evidence-backed alerts. |
| 15 | Caller, behavior, and voice analysis | Add call-pattern/behavior signals, then evaluate diarization and spoof candidates without overstating confidence. |
| 16 | Incident response, privacy, and evidence | User-directed save/export, PII redaction, retention/deletion, hashes, and controlled VoIP isolation/honeypot boundaries are implemented. |
| 17 | End-to-end prototype evaluation | Demonstrate both modes within their constraints, evaluate accuracy/latency/resources/privacy, document results and next steps. |

## Suggested task states

- **Backlog:** not started; acceptance criteria recorded.
- **In progress:** one feature is being implemented.
- **Review:** implementation is ready for user/team inspection against its criteria.
- **Done:** criteria are met and the result is documented.
- **Blocked:** dependency or platform limitation is recorded with a practical alternative.

## Execution rules

- **Discuss first:** Use ChatGPT (Work) to work through the design and acceptance criteria for the active phase. Do not start coding until the user confirms the discussion is complete.
- **Then implement:** Once confirmed, create a focused prompt for Codex to implement the phase. Keep that phase's discussion and implementation in its own chat section in the shared project folder.
- **Shared files and Git:** ChatGPT (Work) and Codex work against the same local project folder. Review and commit changes to the GitHub repository at regular checkpoints; do not assume a change has been committed until verified.
- **One phase at a time:** Follow the roadmap in order and carry decisions and unresolved questions forward explicitly.
- Keep each phase small enough to review independently.
- The controlled VoIP path is the full-audio demonstration path; cellular mode must remain honest about OS/device restrictions.
- Do not freeze model thresholds based on illustrative weights. Record data, measurements, and validation before tuning.
- Preserve the privacy defaults: explicit consent, local processing, transient buffering, user-controlled incident retention, and user authorization before external sharing.
- Track platform-specific support separately; a shared Flutter UI does not imply identical Android/iOS telephony capabilities.
