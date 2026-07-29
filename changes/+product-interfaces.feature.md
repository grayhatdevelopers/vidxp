Expose the shared VidXP application through each supported product surface:

- provide CLI commands for media, index membership, cross-video search, grounded questions, jobs, repositories, actors, and artifacts
- provide a browser UI that reloads cataloged media in fresh sessions, derives search/query controls from capability metadata, confirms model downloads, shows live job progress, submits search with Enter, stops polling terminal jobs, and describes visual-similarity results accurately
- provide a Python indexing and retrieval layer for applications
- provide a versioned HTTP API with OpenAPI, media transfer, readiness, durable jobs, and artifact delivery
- provide local stdio and remote Streamable HTTP MCP with import-ready client configuration, a real protocol self-check, media/index discovery, job polling/retry, and clip download links
- provide a Tauri desktop setup preview that preflights FFmpeg before Python/package downloads, owns its runtime, supervises the same local UI and worker, and leaves FFmpeg bundling behind an explicit provenance/licensing gate
- provide an all-in-one local container using the same repository and worker behavior
