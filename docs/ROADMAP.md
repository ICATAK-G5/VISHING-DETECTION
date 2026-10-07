# Implementation Roadmap

This roadmap reflects the phase sequence agreed in the project chats. **Phase 1 was Development Environment** and was concluded in the earlier chat titled “Set up Phase 1 environment.” The active work here is **Phase 2 — Mobile ↔ Desktop Connection**. The older 17-phase baseline used different phase numbering and is superseded by this sequence.

| Phase | Work | Completion outcome | Status |
|---:|---|---|---|
| 1 | Development environment | Git repository, Flutter/Android Studio setup, Python environment, dependencies, and basic project structure prepared. | Complete |
| 2 | Mobile ↔ Desktop connection | Flutter client pairs to the Python desktop service through encrypted/authenticated local transport; dashboard shows and manages device/connection state. | Complete |
| 3 | Audio input and consent UX | Explicit consent, visible active state, supported user-assisted microphone/speakerphone input, and stop control. | Backlog |
| 4 | Audio streaming gateway | WebSocket audio chunks arrive with session metadata and bounded buffering. | Backlog |
| 5 | Audio preprocessing and VAD | Audio validation/preprocessing and Silero VAD candidate produce speech segments. | Backlog |
| 6 | Speech-to-text | faster-whisper Base produces timestamped transcript; benchmark Small if resources allow. | Backlog |
| 7 | Transcript and live status UI | Transcript/status updates reach desktop and phone with clear failure/quality states. | Backlog |
| 8 | Rule-based phishing indicators | Credential, financial, urgency, impersonation, secrecy, and remote-access indicators produce traceable findings. | Backlog |
| 9 | Contextual model analysis | Local LLM returns schema-validated findings and explanations; untrusted input boundaries are enforced. | Backlog |
| 10 | Fusion and risk reporting | Deterministic engine combines available signals and produces validated levels and evidence-backed alerts. | Backlog |
| 11 | Caller, behavior, and voice analysis | Add call-pattern/behavior signals, then evaluate diarization and spoof candidates without overstating confidence. | Backlog |
| 12 | Incident response, privacy, and evidence | User-directed save/export, PII redaction, retention/deletion, hashes, and controlled VoIP isolation/honeypot boundaries. | Backlog |
| 13 | End-to-end prototype evaluation | Demonstrate both modes within their constraints; evaluate accuracy, latency, resources, privacy, and next steps. | Backlog |

## Phase 2 scope and acceptance

Phase 2 includes the Flutter client, desktop service, and desktop browser dashboard. The desktop service receives mobile pairing requests and authenticated WebSocket connection/status messages. The dashboard is the desktop control surface for QR/manual pairing, approving or rejecting requests, viewing paired/connected/disconnected phones, enabling private-network access, and revoking a device.

The service stays loopback-only by default. Its dashboard and management endpoints remain local-only. The phone listener can bind to detected private IPv4 interfaces only after encrypted transport and device authentication are present and the user enables LAN access. The dashboard must show when a restart is required, prevent pairing until the listener applies the setting, and let the user choose which private address is embedded in a fresh QR code. No public Internet exposure or router port forwarding is part of the design.

Phase 2 is complete when:

- A phone can pair through a short-lived, single-use QR code or manual entry of the desktop address, pairing code, and TLS fingerprint.
- The desktop user approves the pairing; credentials are issued once, stored securely on mobile, and stored only as a salted hash on desktop.
- The phone establishes an authenticated WSS session with the pinned desktop certificate and displays **Connected** only after the service accepts the session.
- Desktop and phone show disconnect/recovery status; the desktop can revoke the phone and prevent reconnection until it pairs again.
- The demonstration shows **PHONE → CONNECTED → DESKTOP** without implying audio capture or analysis.

The user confirmed live QR pairing, desktop approval, an established phone-to-desktop connection, device revocation, and changing the selected Wi-Fi network. Follow-up live checks also confirmed desktop rejection, phone-side removal, dashboard revocation of an active connection, recovery after Wi-Fi interruption, and recovery after the desktop service was stopped and restarted. On desktop revocation, the dashboard shows **Revoked** and the phone reports that it is no longer paired. On phone-side removal, the phone clears its saved connection and the desktop shows **Disconnected**; the desktop can then revoke its retained device record. During Wi-Fi loss, the dashboard shows **Disconnected** and the phone reports **Disconnected / retrying** or **Connecting securely**, with a desktop-unavailable message. When Wi-Fi returns, the phone reconnects automatically and both views return to connected. When the desktop service is stopped, the phone and dashboard device status turn disconnected and the browser dashboard eventually becomes unavailable/offline. After the service is restarted, the existing pairing reconnects and both phone and dashboard return to connected. The Phase 2 connection acceptance outcome is complete. Audio capture and analysis remain out of scope.

## Phase 2 step-by-step status

| Step | Status | Evidence and remaining validation |
|---|---|---|
| Agree architecture, local-only default, and no-audio boundary | Complete | Confirmed before implementation. Audio capture/analysis and cellular-call access are not part of this phase. |
| Flutter Android client | Complete | QR scanner, manual entry, pairing states, connected/disconnected states, secure storage, reconnect loop, and forget-device action are implemented. Dart analysis passed and the Android debug APK built successfully. No microphone permission is requested. |
| Stable device ID and mobile credential storage | Complete | The installation ID persists separately from the approved device token; token and desktop pin use platform-protected storage. |
| Python desktop service and TLS | Complete | FastAPI dashboard listener is loopback-only. Phone HTTPS/WSS listener defaults to loopback and can be enabled on detected private IPv4 addresses after explicit user action. |
| QR pairing, desktop approval, and rejection | Live flow confirmed | User confirmed that approval connects the phone and dashboard, and rejection displays a desktop-rejection status on mobile. Manual fallback and expiry handling are implemented but were not separately reported as live-tested. |
| Authentication, certificate pinning, and desktop revocation | Live flow confirmed | TLS fingerprint pinning, per-device credentials, salted credential hashes, one-time token delivery, and revocation are implemented. User confirmed that revoking an active phone disconnects it, marks it **Revoked** on the dashboard, and shows the no-longer-paired status on mobile. |
| Phone-side removal and device-list state | Live flow confirmed | User confirmed that **Remove pairing from this phone** clears the phone's saved connection and returns it to the initial state; the dashboard retains the device as **Disconnected** with a **Revoke** action. Revoking the retained desktop record then disables that credential. |
| Authenticated WebSocket and connection recovery | Live recovery confirmed | WSS authentication and status keepalive are implemented; a local protocol smoke check passed. User confirmed initial connection, disconnection on revoke, automatic reconnection after Wi-Fi was restored, and disconnection on both sides when the desktop service was stopped. The browser dashboard showed offline while the service was stopped; after restart, the existing pairing reconnected and both sides showed connected. |
| Desktop dashboard and network selection | Live use confirmed | Pending approval, paired-device list, connection state, revocation, restart-required feedback, private-address selection, and fingerprint display are implemented. User confirmed network selection/change. |
| Failure guidance and setup documentation | Complete | Loopback QR codes are detected on mobile with corrective steps; dashboard blocks pairing until the listener setting is applied; manual fields identify their source. Setup, architecture, and roadmap docs were updated. |

### Optional follow-up verification and polish

- Try manual pairing once as a fallback-path check.
- The QR scanner’s visual targeting guide was noted as later UI polish; it does not block the verified QR connection flow.
- The verified client target is Android. An iOS target has not been scaffolded or validated in this repository; that remains platform follow-up work, not a Phase 2 Android acceptance blocker.

## Execution rules

- Discuss and agree on a phase before implementation. The user has approved Phase 2 implementation in the current project chat.
- Work on one phase at a time and carry unresolved decisions forward explicitly.
- Keep cellular protection and controlled VoIP/SIP boundaries separate. The controlled VoIP path is the full-audio demonstration path; cellular mode must remain honest about operating-system restrictions.
- Do not freeze model thresholds based on illustrative weights. Record data, measurements, and validation before tuning.
- Preserve privacy defaults: explicit consent, local processing, transient buffering, user-controlled incident retention, and user authorization before external sharing.
- Track platform-specific support separately; a shared Flutter UI does not imply identical Android/iOS call or audio capabilities.
- Do not store secrets, private recordings, or generated model weights in Git. Review and commit changes only at user-directed checkpoints.
