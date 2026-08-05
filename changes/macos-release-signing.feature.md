Sign and notarize the macOS desktop release build so the DMG installs and launches without Gatekeeper warnings:

- code-sign the app, the bundled `uv` sidecar, and the inner executable with a Developer ID Application certificate under the hardened runtime
- notarize with Apple and staple the ticket, using an App Store Connect API key
- apply signing only to release-candidate builds; pull request, push, and fork builds stay unsigned and need no Apple secrets
