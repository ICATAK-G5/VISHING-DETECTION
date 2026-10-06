# Repository guidance

- Follow `docs/PROJECT_ARCHITECTURE.md` and `docs/ROADMAP.md` as the project source of truth.
- The user discusses and confirms a roadmap phase before implementation begins. Keep each phase's planning and implementation in its own project chat section.
- Keep the desktop service local-only by default. Do not bind it to a network interface or expose it beyond loopback until authentication and encrypted transport are implemented.
- Preserve separate Cellular Protection and Controlled VoIP/SIP mode boundaries. Do not claim unrestricted cellular-call audio access.
- Keep analysis on the desktop. The mobile app is a client and must show consent/status clearly.
- Do not store secrets, private call recordings, or generated model weights in Git.
- Make focused changes and update the relevant setup or architecture documentation when behavior or requirements change.
