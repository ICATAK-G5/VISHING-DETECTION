# Phase 2 implementation prompt

Use the following implementation brief for **Phase 2 — Mobile ↔ Desktop Connection** in this repository. Phase 1 was the development environment and basic project setup, completed in the earlier project chat. Do not relabel this work as requirements planning.

> Work in `C:\Users\sange\Documents\ICTAK G5 project\VISHING DETECTION` and implement Phase 2: a Flutter phone client, a Python/FastAPI desktop receiving service, and a browser-based desktop dashboard. Read `AGENTS.md`, `docs/PROJECT_ARCHITECTURE.md`, `docs/ROADMAP.md`, `docs/PROJECT_STRUCTURE.md`, `backend/README.md`, and `mobile_app/README.md` before changing code. Preserve existing local changes and secrets. Keep the implementation focused on pairing, authentication, encrypted connection, and status; do not add call audio capture or analysis in this phase.
>
> **Architecture and boundaries**
>
> - Flutter/Dart is the phone client. Python 3.12, FastAPI, and Uvicorn run on the Windows desktop. The existing browser dashboard is the desktop control surface; do not introduce a separate Windows desktop UI framework.
> - The phone initiates the connection. The desktop receives pairing requests and authenticated WebSocket sessions. The dashboard displays pending pairing, approval, paired devices, connected/disconnected state, last seen, and revocation.
> - Keep the dashboard and its management API bound to loopback. Keep the TLS phone listener bound to loopback by default. Only after TLS and authentication are implemented may the user enable the phone listener on a detected private IPv4 interface. Never default to `0.0.0.0`, port-forwarding, or public Internet access. Dashboard management endpoints must remain local-only even when LAN phone access is enabled.
> - Use HTTPS/WSS for the phone protocol. Pair with a short-lived, single-use QR code; include a manual fallback requiring the same desktop address and full SHA-256 TLS certificate fingerprint. The user must approve each pairing request in the desktop dashboard. A QR code is not itself device authentication.
> - Generate a random stable device ID for each app installation. Keep it separate from the device credential. Issue a unique credential after desktop approval, store only a strong salted hash on desktop, and store the credential in mobile platform secure storage. Support revocation and re-pairing.
> - Pin the desktop TLS certificate fingerprint conveyed during pairing. Make certificate changes visible and require re-pairing. Do not silently accept arbitrary self-signed certificates.
> - Show connection states clearly on both clients. Retry with backoff, recover after network interruptions, and surface expired pairing, rejected pairing, revoked credential, and certificate mismatch. Do not claim that a connected phone is capturing audio.
> - Keep Cellular Protection and Controlled VoIP/SIP as separate operating modes. Do not promise general cellular-call audio access. Analysis remains on the desktop. Do not request microphone or telephony permissions for this connection-only work.
>
> **Implementation sequence**
>
> 1. Inspect the current checkout and existing files; do not overwrite unrelated local changes.
> 2. Establish the Flutter Android app shell in `mobile_app/`, including the connection screen, stable device ID, QR scanning, manual pairing entry, secure credential storage, pairing approval wait state, connection indicator, reconnect, and forget-device action. Keep platform-specific telephony work out of scope.
> 3. Implement the Python service under `backend/src/vishing/`: loopback health/dashboard service, separate TLS listener for phone traffic, per-user certificate and persistent desktop identity, short-lived pairing lifecycle, explicit desktop approval/rejection, device authentication and revocation, and an authenticated versioned WebSocket carrying only connection keepalive/status messages.
> 4. Build the browser dashboard in the existing FastAPI service. Provide QR/code generation, expiry display, pending phone approval/rejection, paired device list, accurate live status and last-seen, revocation, private-network setting, detected LAN address, and TLS fingerprint. Keep management operations inaccessible to remote clients.
> 5. Connect the Flutter app to the desktop protocol. Use certificate pinning for both pairing HTTPS and WSS. Keep pairing credentials, database contents, TLS private keys, recordings, and generated model weights outside Git.
> 6. Update dependency records, startup instructions, architecture/roadmap/status documentation, and this phase's acceptance criteria. Use the project's existing local Python environment when available. If a required dependency cannot be installed because network access is unavailable, preserve the source changes and report that limitation precisely.
>
> **Acceptance criteria**
>
> - The phone can scan the desktop QR or enter the fallback details and submit a pairing request.
> - The desktop dashboard shows the waiting phone, and the phone remains visibly pending until the user approves or rejects it on desktop.
> - Approval gives the phone a persistent device credential once; an unpaired or revoked phone cannot authenticate its WebSocket.
> - Both dashboards distinguish paired from connected. Stopping the service or dropping Wi-Fi shows a disconnect and reconnects without creating a false connected state.
> - Revoking a device closes its live connection and requires a new pairing.
> - The desktop dashboard stays loopback-only. The phone listener starts loopback-only and can be enabled on a private local interface only through an explicit desktop setting after TLS/authentication exist.
> - The success demonstration is `PHONE → CONNECTED → DESKTOP`. No audio capture or analysis is implied.
>
> Make focused changes, keep credentials and user data out of Git, and report completed work, changed files, how to start each component, limitations, and any environment issue preventing an end-to-end demonstration. Do not commit or push unless the user asks.

