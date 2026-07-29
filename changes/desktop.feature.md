### Desktop installer

- Added a minimal Tauri desktop installer and supervisor that installs exact
  capability extras from PyPI into an app-owned uv runtime.
- Added atomic runtime activation, optional model preparation, platform
  app-data paths, loopback-only UI launch, and graceful local-worker shutdown.
- Kept FFmpeg packaging as an explicit provenance and licensing gate.
