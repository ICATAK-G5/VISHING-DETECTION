# Project Architecture and Requirements

Migrated from the ChatGPT conversation **Cybersecurity Project Analysis**. This document consolidates the initial feasibility analysis with the later clarifications. Where they differ, the later, finalized cross-platform design supersedes the earlier Android-only proposal.

## 1. Purpose and scope

Prototype an AI-assisted voice-phishing detection and incident-support system. Analyze call content, social-engineering tactics, conversational behavior, voice authenticity signals, caller reputation, and call patterns. Present a live risk explanation and recommended action to the user. When the user chooses, preserve a privacy-conscious incident package suitable for review and possible submission.

The initial prototype handles one phone at a time, with analysis on one Windows desktop/laptop. Distributed multi-device service is future work. The reference development machine described in the source chat has an Intel i7-11800H, 16 GB RAM, and RTX 3050 Ti Laptop GPU with 4 GB VRAM; model sizes and concurrency must be benchmarked against this constraint.

## 2. Operating modes

### Cellular Protection Mode

For ordinary cellular calls. Use platform-supported call screening/caller information, reputation and call-history signals, and user-visible alerts. An opt-in speakerphone-to-microphone audio path may be used for a prototype demonstration when the user enables it and the device supports it.

Do not promise universal capture of both sides of arbitrary cellular calls. Android call-screening APIs do not provide general-purpose access to cellular-call audio; audio capture depends on device, OS, routing, and permissions. iOS third-party apps likewise cannot generally inspect live audio for a cellular call carried by another app. Cellular audio capabilities are platform-dependent.

### Controlled VoIP/SIP Mode

The complete end-to-end demonstration mode. A controlled VoIP/SIP service carries the call and provides separate caller/user audio streams to the desktop analysis service. This makes real-time transcription, analysis, risk alerts, controlled isolation, and optional honeypot experimentation feasible without relying on unsupported cellular interception.

## 3. Component architecture

```text
MOBILE CLIENT (Flutter; native Kotlin/Swift modules as needed)
  - consent and mode selection
  - supported call information and user-assisted capture
  - audio streaming and visible analysis status
  - live risk alerts, transcript/report views, user actions
                  │ authenticated encrypted stream/API
                  ▼
DESKTOP SYSTEM (Python services)
  Audio Gateway → buffering / preprocessing → VAD → speech-to-text
                                    ├→ speaker separation/diarization
                                    ├→ voice spoof/authenticity analysis
                                    ├→ content and social-engineering analysis
                                    ├→ caller/call-pattern analysis
                                    └→ PII detection and redaction
                                             ▼
                               Feature Fusion + Risk Engine
                                  ├→ live explanation/report → phone
                                  ├→ local incident store → desktop
                                  └→ user-authorized evidence export

CONTROLLED VoIP/SIP: caller ↔ controlled gateway ↔ user client
                                  └→ desktop analysis; optional isolated honeypot
```

Desktop responsibilities: audio gateway, model execution, feature fusion, deterministic risk scoring, incident records, evidence generation, and optional controlled VoIP/honeypot services. For Phase 2, the desktop also hosts a local browser dashboard to create and approve phone pairings, display connection state, and revoke devices. A loopback dashboard/API listener is separate from the HTTPS/WSS phone listener; dashboard management stays loopback-only when the phone listener is explicitly enabled on detected private IPv4 interfaces. The dashboard selects which private address is encoded into a phone pairing QR. LAN setting changes require a service restart, and pairing is blocked until the active listener matches the saved setting. Mobile responsibilities: consent, supported call integration, user-controlled capture/streaming, alerts, and viewing reports. Do not place core AI analysis on the phone for the prototype.

### Phase 3 audio transport boundary

Phase 3 joins explicit consent, call-state-triggered microphone capture, authenticated WSS transport, and desktop reception. User Start arms a visible Android microphone foreground service while the microphone remains off during Android idle/ringing states. Capture begins at `CALL_STATE_OFFHOOK`; Android defines off-hook as dialing, active, or on hold, so the app cannot tell whether an outgoing call has been answered. The service notification distinguishes **Armed · microphone off** from **Capturing microphone** and has a stop action. Capture stops at idle/ringing, user stop, network loss, or failure. Android's foreground service permits continued capture after the app is backgrounded when its permission and service requirements are met, but it does not grant access to cellular call audio. Cellular Protection may pick up speakerphone audio acoustically only when Android and the device permit it; ordinary apps are not guaranteed to receive voice-call input or caller identity. Validate on the physical target phone and clearly report when unavailable. The transport should be source-neutral enough to accept a future Controlled VoIP/SIP source. See [Android microphone foreground-service requirements](https://developer.android.com/develop/background-work/services/fgs/service-types#microphone), [background-start restrictions](https://developer.android.com/develop/background-work/services/fgs/restrictions-bg-start), [audio-input sharing rules](https://developer.android.com/media/platform/sharing-audio-input), and [Android call-state listener](https://developer.android.com/reference/android/telephony/TelephonyCallback.CallStateListener).

Audio sessions reuse the paired device credential and pinned certificate through a separate `/api/v1/mobile/audio` WSS endpoint. The phone accepts the desktop TLS connection only when the leaf certificate's SHA-256 fingerprint exactly matches the value saved at user-approved pairing; it does not rely on hostname/IP SAN matching because private desktop addresses can change between pairing and later connections. A changed certificate requires re-pairing. The initial protocol uses versioned JSON controls and binary 20 ms frames: mono PCM16 at 16 kHz (640-byte payload), with sequence and monotonic capture timestamp in the frame header. The phone does not accumulate a send queue: it drops frames under backpressure and stops capture if congestion persists. The desktop validates the authenticated device, session, format, frame size, sequence, and timestamp. It holds no more than five seconds of audio in a bounded in-memory preview ring; playback is explicit and served only through the loopback dashboard. No audio is written to disk or processed into VAD/transcript/risk findings in Phase 3. The dashboard shows armed/receiving/disconnected/stopped states, signal level, sequence gaps, dropped frames, and desktop-observed arrival jitter. Phone and desktop monotonic clocks are not synchronized, so raw timestamp subtraction is not presented as one-way latency. Phase 4 runs VAD over validated desktop audio; Phase 5 performs speech recognition. Phase 9 combines specialist results using the deterministic risk engine, with voice security and caller/behavior signals added in Phases 13–14. Controlled VoIP/SIP is implemented in Phase 15 and must provide a compatible source/channel description.

**Approved Phase 3 scope update — 2026-10-09 (revised):** In addition to cellular protection, Phase 3 includes direct phone-to-desktop WebRTC audio and controlled Phone A ↔ Phone B in-app calling. Both phones authenticate to one paired desktop over the existing `/api/v1/mobile/ws`; the server authorizes each call and issues a unique `call_id` and fixed roles (`far` for the caller/Phone A, `near` for the receiving/protected target/Phone B). The mobile UI lists paired phones at once with per-device call/end-call actions; users do not choose near/far roles. The target accepts explicitly. The desktop relays SDP and ICE only; phone-to-phone media travels peer-to-peer over WebRTC. Only the target phone forwards analysis audio: its local microphone as `near` and the decoded incoming caller track as `far`, over separate authenticated `/api/v1/mobile/audio` WSS connections keyed by device, call, and role. The 656-byte VDA1 frame remains unchanged (16-byte header plus 640 bytes of 16 kHz mono PCM16), paced at 50 frames/second. The dashboard displays the two tracks separately and groups them by call. Controlled calls default to Android's private earpiece route; speakerphone is not forced on both devices because acoustic feedback can produce a loud, high-pitched tone. This routing change is pending physical-device verification. Starting an outgoing controlled app call stops Cellular Protection on that caller phone to avoid a duplicate capture session. Android's generic cellular `CALL_STATE_OFFHOOK` remains unable to distinguish outgoing cellular calls from incoming/active calls. Either participant can explicitly send a recorded clip over an ordered WebRTC data channel after recipient consent; the recipient checks its SHA-256 digest and may upload it to the desktop for batch analysis. Desktop clips are normalized to WAV, stored locally for up to 24 hours, and removable in the dashboard. `scripts/replay_audio.py` supports Phone X diagnostic replay at 50 frames/second. Direct-to-desktop WebRTC remains a separate single-phone stream mode and assigns its stream the `near` role automatically. Initial WebRTC connectivity targets host ICE candidates on the trusted same LAN; STUN/TURN and remote network access remain future work. The dashboard preview retains at most 60 seconds per live stream in RAM. Cellular Protection remains a distinct, platform-limited mode and does not promise SIM-call audio access. Phase 3 adds no VAD, transcription, or risk analysis.

## 4. Technology choices

| Area | Initial choice | Notes |
|---|---|---|
| Mobile UI | Flutter / Dart | Shared Android and iOS UI; use Kotlin/Swift through platform channels for native APIs. |
| Android integration | Kotlin | Telecom/call-screening APIs, permissions, foreground services, and audio APIs where allowed. |
| iOS integration | Swift | VoIP/CallKit and supported caller-ID/blocking integration; no promise of cellular-call audio access. |
| Desktop/backend | Python 3.11/3.12, FastAPI, Uvicorn | Local services and API; use WebSocket for the first live audio prototype. |
| Audio/ML | PyTorch, NumPy, SciPy; librosa as needed | Keep the first pipeline small and benchmark CPU/GPU memory. |
| Controlled Digital Audio | Flutter WebRTC + aiortc/PyAV | Approved Phase 3 path: WSS signaling plus ICE/DTLS-SRTP media, initially validated on the trusted same LAN. |
| Storage | Local database and user-controlled incident files | Select a concrete database during implementation; minimize stored personal data. |

The earlier Android 11+ benchmark is not a project-wide restriction. Device support is capability-based and platform-dependent; publish minimum/maximum targets only after implementation testing.

## 5. Analysis pipeline and candidate models

Run independent specialist components where useful. Normalize their outputs into evidence-bearing signals, then combine them in a deterministic fusion/risk engine. Model names below are recommendations captured in the source conversation, not installed dependencies or final benchmark results.

| Function | Candidate | Status/notes |
|---|---|---|
| Voice activity detection | Silero VAD | Required pipeline component; lightweight candidate. |
| Speech-to-text | faster-whisper, start with Base; benchmark Small | Required; local processing preferred. |
| Speaker diarization | pyannote.audio Community-1 | Recommended for mixed audio; separate controlled VoIP channels may make it unnecessary. |
| Voice spoof/deepfake analysis | AASIST | Advanced; report an authenticity/anomaly signal, never certainty from one score. |
| Social-engineering/context analysis | Rules plus a local instruct model; later conversation recommendation identifies Qwen3-4B | Structured findings; benchmark against the 4 GB VRAM constraint. An earlier draft mentioned Qwen 2.5 3B; the later finalized recommendation supersedes it. |
| PII detection/censoring | Dedicated detection/redaction subsystem; exact model not finalized | Redact transcripts/reports and, if technically validated, audio. Keep original evidence access-controlled and user-authorized. |
| Summarization/report explanation | Qwen3-4B candidate | May explain supported findings; must not invent evidence or make the final risk decision. |
| Risk fusion | Project-owned deterministic engine | Required; combines signals and calibrated thresholds. |
| Honeypot dialogue | Qwen3-4B candidate, isolated behind strict policy | Advanced and controlled VoIP only; evidence collection, not retaliation. |

### Signals to analyze

1. **Content:** transcript, requests, claims, and contextual consistency.
2. **Social engineering:** urgency, authority impersonation, coercion, secrecy, isolation, trust-building, and verification bypass.
3. **Credential/financial requests:** OTP, PIN, passwords, CVV, recovery codes, account details, money/UPI/bank transfers, gift cards, cryptocurrency, fake refunds/fines/investments.
4. **Device compromise:** remote-access tools, screen sharing, app/APK installation, links, QR codes, and browser extensions.
5. **Conversation behavior:** interruption, pressure, escalation, repetition, evasion, refusal of independent verification, and turn-taking patterns.
6. **Voice/audio:** speech rate, pitch/prosody, pauses, voice consistency, background noise, clipping, compression artifacts, acoustic transitions, repeated recordings, and synthetic-speech/spoof indicators.
7. **Caller identity/reputation:** known contact status, local reports, number reputation, caller-ID information, and spoofing indicators. A displayed number is not proof of identity.
8. **Call patterns/context:** repeat frequency, time, duration, prior incidents, user response, and supported network verification metadata such as STIR/SHAKEN where available.

These are signals, not standalone proof that a caller is malicious. Show why a finding was made and preserve uncertainty, especially for voice authenticity and caller identity.

## 6. Fusion, risk, and reporting

Each specialist returns structured findings with a value, confidence/quality, timestamp or transcript span where possible, and explanation. The fusion layer handles missing/low-quality signals and produces a deterministic score and level. Example weights in the source conversation (OTP/password +25, transfer +25, remote access +20, authority impersonation +15, urgency +10, threats +15, secrecy +10, suspicious history +15, voice anomaly +15) are illustrative engineering parameters only. Tune and validate them on a documented dataset; do not present the score as a scientifically established probability.

Example initial bands from the conversation: 0–29 LOW, 30–59 MEDIUM, 60–79 HIGH, 80–100 CRITICAL. Thresholds remain configurable pending evaluation. The live report should include caller context, threat level/score, detected indicators, supporting transcript/audio evidence, model quality/uncertainty, and a recommendation such as disconnect or verify through an independent channel. Send the report to the phone and retain it on desktop according to the chosen retention setting.

The user remains in control. A high score can trigger a prominent warning and disconnect prompt; avoid silently terminating calls based only on a model result.

## 7. Incident response and evidence

### User-directed incident flow

```text
Risk detected → warn user → user chooses disconnect / continue monitoring / save incident
                                      ↓
                           generate reviewable evidence package
                                      ↓
                           user authorizes any external submission
```

An already-established cellular call cannot generally be transferred by an ordinary mobile app to the desktop or a honeypot. In controlled VoIP/SIP mode, future routing/isolation can be part of the controlled gateway design. Do not describe this as redirection of arbitrary cellular calls.

An optional AI honeypot is limited to a controlled environment and must not disclose the user's identity, contact details, location, device/network data, OTPs, passwords, PINs, CVV, banking data, tokens, or documents. It must not transfer money, open links/downloads, install software, impersonate authorities, threaten callers, or reveal detection internals. Its purpose is evidence collection, not retaliation.

An evidence export may contain incident metadata, transcript, risk findings, a report, user-selected audio, indicators (phone numbers, URLs, emails, payment identifiers), and SHA-256 hashes. Preserve provenance and distinguish original from redacted copies. Generate a police/cybercrime-ready report for user review; do not automatically submit to authorities absent an officially authorized integration and explicit user authorization.

## 8. Privacy and security requirements

- Local desktop processing by default; no external AI upload unless separately chosen and disclosed.
- Explicit consent and visible indication before audio analysis/recording. Obtain any required participant consent for the deployment jurisdiction.
- Data minimization: transient audio buffer for live analysis; no permanent recording of every call. Save an incident only through a clear user choice and retention policy.
- Encrypt phone-to-desktop traffic (HTTPS/WSS/TLS); authenticate devices and protect local credentials. Do not expose the desktop gateway to the public Internet by default.
- Pairing uses an expiring, single-use QR code or manual entry of the desktop address, pairing code, and SHA-256 certificate fingerprint. The user approves each phone on the desktop. Pin the desktop certificate on the phone, issue a distinct per-device credential, keep only a salted credential hash on desktop, and support revocation.
- Separate original evidence from redacted exports. Offer user-controlled PII censorship for transcript/report and validated audio redaction where feasible.
- Least privilege on mobile; request only permissions needed for the active feature. Microphone foreground-service requirements are platform/version-specific.
- Access controls, deletion/export controls, secure local storage, audit of evidence actions, and safe handling of model inputs/outputs.
- Treat LLM transcripts and caller utterances as untrusted data. The LLM cannot invoke arbitrary tools or override the system's safety policy.

## 9. Constraints and non-goals

- No universal cellular audio interception or guaranteed two-sided capture.
- No arbitrary transfer/redirect of an active cellular call to a desktop or police/cyber cell.
- No claim that voice spoofing, a phone number, or an LLM score proves a caller's identity or intent.
- No automatic police reporting without an authorized interface and user authorization.
- No multi-device distributed service in the first prototype.
- Avoid large models and unnecessary cloud costs; benchmark each candidate on the stated laptop before adoption.
