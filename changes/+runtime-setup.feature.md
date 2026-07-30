Make local setup and execution explicit and observable:

- add `vidxp init` to verify FFmpeg, ffprobe, `libx264`, and `aac`, and request consent before a supported package-manager command
- make `vidxp prepare` disclose every missing pinned model, its expected bytes, the cache location, total additional space, and live byte progress before downloading
- use bounded HTTP model downloads instead of Xet transfers that can remain parked at zero bytes
- keep `vidxp doctor` read-only and report Python, provider, codec, worker, storage, and prepared-model readiness separately
- prevent indexing, API, or MCP first requests from silently downloading models
- place repositories and model caches in the operating system's per-user VidXP data root instead of the shell's current directory
- sample scenes by time and source frame rate with independent actor cadence and CLI, UI, API, and MCP controls
- skip dialogue cleanly for videos without audio instead of failing the entire index
- avoid an arbitrary free-memory refusal, allow realistic worker startup time, hide the Windows worker console, prevent duplicate indexing previews, and stop owned workers when the local UI, API, or MCP process exits
- pass local-worker settings through a one-use owner-readable bootstrap without HTTP credentials, with startup backoff, bounded log rotation, stale-process cleanup, and executable/provider identity checks
