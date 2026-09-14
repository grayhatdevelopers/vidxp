---
name: vidxp-ingest-video
description: Use VidXP to upload, import, register, and automatically index video files through its MCP tools. Trigger for requests to add, upload, ingest, import, register, or index one or more videos, including attached videos and accessible local paths, even when the user does not name VidXP. Do not trigger for editing, transcoding, or searching a video that is already indexed.
---

# Ingest video with VidXP

## Workflow

1. Resolve the `vidxp` MCP tools and call `get_workspace`. Do not import a video
   that is already registered or indexed.
2. Choose indexable capabilities from the workspace. Use `speech` and `scene`
   for ordinary content retrieval. Add `action` when the request depends on
   actions or events spanning multiple frames. Add `actor` only when anonymous
   recurring-face clusters are wanted; it does not identify people by name.
3. Call `get_runtime_readiness`. If selected models are missing, submit
   `prepare_models`, use `wait_job` with its observation token for subsequent
   bounded waits, then fetch `get_job` once when terminal.
4. Use `ingest_local_media` for one to ten paths accessible to VidXP; otherwise
   use `create_media_upload` and give the returned link to the user. Keep
   `index_after_import` enabled unless registration-only behavior was requested.
5. Poll the returned ingestion or upload ID with `get_media_ingestion` or
   `get_media_upload`. Honor its poll interval, reuse the same identifiers, and
   do not resubmit unchanged work.
6. Stop at a terminal state and report each file's state, media ID, index job,
   and searchable snapshot or generation. If indexing fails after registration,
   retry with `start_indexing`; do not upload the file again.
7. If the request also asks about the video, continue directly into the VidXP
   evidence workflow once it is searchable.

## Long operations

- Tell the user that model preparation and indexing can take several minutes.
- Update when the stage changes or about once per minute; do not narrate every
  status check or invent an ETA.
- Treat files independently so one failure does not hide successful siblings.
