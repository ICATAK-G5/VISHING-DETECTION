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

Desktop responsibilities: audio gateway, model execution, feature fusion, deterministic risk scoring, incident records, evidence generation, and optional controlled VoIP/honeypot services. Mobile responsibilities: consent, supported call integration, user-controlled capture/streaming, alerts, and viewing reports. Do not place core AI analysis on the phone for the prototype.

## 4. Technology choices

| Area | Initial choice | Notes |
|---|---|---|
| Mobile UI | Flutter / Dart | Shared Android and iOS UI; use Kotlin/Swift through platform channels for native APIs. |
| Android integration | Kotlin | Telecom/call-screening APIs, permissions, foreground services, and audio APIs where allowed. |
| iOS integration | Swift | VoIP/CallKit and supported caller-ID/blocking integration; no promise of cellular-call audio access. |
| Desktop/backend | Python 3.11/3.12, FastAPI, Uvicorn | Local services and API; use WebSocket for the first live audio prototype. |
| Audio/ML | PyTorch, NumPy, SciPy; librosa as needed | Keep the first pipeline small and benchmark CPU/GPU memory. |
| Later transport | WebRTC | Consider after the WebSocket pipeline is proven; adds real-time media/network complexity. |
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
