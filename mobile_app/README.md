# Flutter phone client

The Flutter app is a connection client only. Its current screen supports QR scanning, manual pairing details, desktop approval status, secure connection status, retry, and forgetting a desktop. It does not request microphone permission or capture call audio.

The Android wrapper is included. From this folder, run `flutter pub get`, then `flutter run` with an Android device selected. The app needs camera access for QR scanning and internet access for the desktop connection.

To connect a physical phone, connect it and the desktop to the same trusted Wi-Fi. In the desktop dashboard, enable **Allow paired phones on local network**, stop the desktop service in its terminal (Ctrl+C), and start it again with `scripts/start_desktop.ps1`. Wait until the dashboard says **LAN enabled**. Choose the desktop's address on that Wi-Fi, then click **Start pairing** and scan that newly generated QR code. Do not reuse a QR generated before the service restart; it may point to `127.0.0.1`, which refers to the phone itself. After a successful scan, the dashboard displays the phone request for approval.

If using manual pairing, copy the **Phone address**, **Fallback code**, and **TLS fingerprint** from the desktop dashboard. The mobile form describes each source. The phone address must be the desktop's Wi-Fi IPv4 address, not `127.0.0.1`.

The mobile client pins the desktop certificate fingerprint carried by the pairing QR. A device ID identifies this app installation; a separate device credential authorizes it. The credential and desktop details are stored by `flutter_secure_storage`.

## Pairing and disconnect actions

- **Reject on desktop:** rejects the pending request before a device credential is issued. The phone shows that the desktop rejected the request; start a fresh pairing to try again.
- **Revoke on desktop:** invalidates the phone's credential on the desktop and closes its active connection. The dashboard marks the device **Revoked**. The phone reports that it is no longer paired; use **Forget this desktop and pair again** to clear its saved desktop details from the phone before pairing again.
- **Remove pairing from this phone:** clears the saved desktop address, certificate pin, and credential from this phone. It does not delete the desktop's device record; the dashboard shows that device as **Disconnected** and still offers **Revoke**. Revoke it on the dashboard as well if you want the desktop to invalidate that device credential.

These actions are intentionally separate: forgetting locally clears this phone's stored connection details, while revoking disables the credential at the desktop so it cannot reconnect.

## Network interruption and recovery

If Wi-Fi is interrupted while paired, the app shows **Disconnected / retrying** or **Connecting securely** and reports that the desktop is unavailable. It retries automatically with backoff. After the phone can reach the desktop again, it reauthenticates and returns to **Connected** without requiring a new QR pairing. The desktop dashboard shows **Disconnected** during the interruption and **Connected** after the session is restored. This recovery behavior has been confirmed during a live Wi-Fi interruption. If the desktop service is stopped, the phone becomes disconnected and continues trying to reach it; the browser dashboard becomes unavailable while the service is stopped. When the service is restarted, the existing pairing reconnects and both phone and dashboard return to **Connected**. This has also been confirmed live.

iOS uses the same Flutter UI conceptually, but this checkout does not yet include an iOS target. Platform call capabilities remain separate and do not imply general access to cellular-call audio.
