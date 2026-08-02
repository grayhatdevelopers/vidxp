---
name: vidxp-ingest-video
description: Use VidXP to upload, import, register, and automatically index video files through its MCP tools. Trigger for requests to add, upload, ingest, import, register, or index one or more videos, including attached videos and accessible local paths, even when the user does not name VidXP. Do not trigger for editing, transcoding, or searching a video that is already indexed.
---

# Ingest video with VidXP

Use the VidXP MCP tools for the complete ingestion workflow. Do not replace them
with an ad hoc upload script, base64 media in tool arguments, or manual database
changes.

## Workflow

1. Resolve the declared `vidxp` MCP dependency. When the host supports lazy
   connector or tool discovery, search for VidXP before concluding it is absent.
   If its tools still cannot be loaded, say that the VidXP connector is not
   connected and stop instead of inventing a substitute.
2. Call `get_workspace` to inspect existing media and index coverage. Avoid
   importing the same video again when it is already registered or indexed.
3. Call `get_runtime_readiness` before automatic indexing. If required model
   artifacts are missing, call `prepare_models` and poll that job with `get_job`
   until it reaches a terminal state.
4. Select ingestion from the tools actually exposed by the current transport:
   - When `ingest_local_media` is available and the video has a filesystem path
     accessible to VidXP, pass one to ten paths and poll only
     `get_media_ingestion`.
   - Otherwise call `create_media_upload`, give the returned upload link to the
     user, and poll only `get_media_upload` after files are selected.
5. Keep `index_after_import` enabled unless the user explicitly asks to register
   media without indexing. Reuse the same idempotency key when retrying the same
   request.
6. Stop polling when the returned aggregate state is terminal. Treat each file
   independently so one failure does not hide successful siblings.

## Long-running operations

- Tell the user before polling that model preparation and indexing can take
  several minutes, depending on video length, selected capabilities, hardware,
  and whether models are already cached.
- Honor a server-provided polling interval when present. Otherwise poll after
  about 5 seconds initially and back off to about 15 seconds for sustained work.
  Never busy-loop or create a shell script merely to wait.
- Keep using the original job, ingestion, and idempotency identifiers. Do not
  submit duplicate work because a state remains unchanged.
- Give a concise update when the lifecycle stage or measured progress changes,
  and approximately once per minute during a long unchanged stage. Do not emit
  every poll result.
- Report only progress and timing returned or directly measured by VidXP. Do not
  invent an ETA. If the interaction must stop before completion, provide the
  recovery identifier needed to resume polling later.

## Recovery and output

- If import fails, report its structured error and remediation.
- If indexing fails after registration, do not upload the file again. Use the
  returned media ID with `start_indexing` when the user wants to retry.
- Report each filename, lifecycle state, media ID, index job, and searchable
  snapshot or generation when present.
- State clearly whether each video is merely uploaded, registered, indexing, or
  indexed and searchable.
