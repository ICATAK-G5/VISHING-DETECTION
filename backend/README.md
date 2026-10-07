# Desktop service

The desktop service is a local Python/FastAPI application. It serves a browser dashboard on `http://127.0.0.1:8000` and a separate TLS-protected phone API on port `8443`.

## Install and start

From the repository root in PowerShell:

```powershell
python -m pip install -r .\backend\requirements.txt
& .\scripts\start_desktop.ps1
```

Open `http://127.0.0.1:8000` on the desktop. Keep the terminal open while the service is running; press Ctrl+C to stop it.

The browser dashboard listener always binds to loopback. The HTTPS/WSS phone listener also binds to loopback by default. To pair a physical phone:

1. Connect the desktop and phone to the same trusted Wi-Fi network.
2. Enable **Allow paired phones on local network** in the dashboard.
3. Stop the desktop service in its terminal (Ctrl+C), then start it again with `scripts/start_desktop.ps1`.
4. Wait for the dashboard to show **LAN enabled**. If it says a restart is required, do not try to pair yet.
5. Choose the desktop IPv4 address on the same Wi-Fi as the phone, then click **Start pairing**. Scan the new QR code; codes created before the restart are stale and may contain `127.0.0.1`.
6. The dashboard shows the phone request while it waits. Approve or reject it there.

When LAN access is enabled, the secure listener binds only to detected private IPv4 addresses, not to a wildcard interface. The dashboard lets the user choose which address is embedded in the QR code. `127.0.0.1` is only for a client on the desktop itself and cannot be used by a physical phone.

## Local files and network

The service creates its TLS certificate/key, stable desktop ID, connection settings, and pairing database in `%LOCALAPPDATA%\VishingDetection` on Windows (or the equivalent per-user data folder elsewhere). The private key and device credential hashes are not stored in this repository. The mobile app receives its device token once after desktop approval and stores it using platform-protected secure storage.

The desktop creates a self-signed certificate. The QR code carries its SHA-256 certificate fingerprint, and the Flutter client accepts only that pinned certificate for the pairing and WSS connection. The fallback manual flow requires the same full fingerprint, phone address, and one-time code; the dashboard labels each value. If the certificate is deleted or replaced, pair the phone again after checking the new fingerprint.

The dashboard and its management routes accept only loopback requests, including when the phone listener is enabled. The phone listener is TLS-only. Its pairing code expires after three minutes and is single-use; approving a pairing request issues a device-specific credential. Revoke a phone from the dashboard to disable that credential and close its live connection. The dashboard retains the device record as **Revoked**; the phone must clear its saved desktop details and pair again before it can reconnect. If the phone removes its own pairing first, the dashboard retains the device as **Disconnected** until it is revoked or paired again. A temporary Wi-Fi interruption leaves the paired device marked **Disconnected** while the mobile client retries; when the network returns, the authenticated session is restored automatically and the dashboard returns to **Connected**. This recovery path has been confirmed live. Stopping the desktop service disconnects the phone and stops the dashboard server, so the browser page eventually becomes unavailable/offline. After the service is restarted, the existing pairing reconnects automatically and both phone and dashboard return to **Connected**; this restart recovery has been confirmed live.

Do not forward the phone port from a router or expose it to the public Internet. A local-network connection is intended for a private network trusted by the user. This phase carries pairing and status messages only; it does not capture or send call audio.

## Configuration

`VISHING_HTTP_PORT` and `VISHING_SECURE_PORT` can change the default dashboard and phone API ports. `backend/requirements.txt` is the dependency record. Generated credentials and pairing data remain outside Git.
