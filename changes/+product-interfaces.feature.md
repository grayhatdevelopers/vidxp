Expose the shared VidXP application through each supported product surface:

- provide CLI commands for media, index membership, cross-video search, grounded questions, jobs, repositories, actors, and artifacts
- provide a browser UI that reloads cataloged media in fresh sessions, derives search/query controls from capability metadata, confirms model downloads, shows live job progress, submits search with Enter, stops polling terminal jobs, and describes visual-similarity results accurately
- provide a Python indexing and retrieval layer for applications
- provide a versioned HTTP API with OpenAPI, media transfer, readiness, durable jobs, and artifact delivery
- provide local stdio and remote Streamable HTTP MCP with import-ready client configuration, a real protocol self-check, media/index discovery, job polling/retry, and clip download links
- provide a Tauri desktop application that preflights FFmpeg with native consent, provisions selected capabilities and optional browser dependencies, supports a custom model location and deferred downloads, opens configured browser profiles directly, owns its runtime from a native system tray with full shutdown on Quit, builds as a Windows GUI executable, and hides supervised child consoles
- provide an all-in-one local container using the same repository and worker behavior
