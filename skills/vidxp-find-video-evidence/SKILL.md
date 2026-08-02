---
name: vidxp-find-video-evidence
description: Use VidXP to search indexed videos, answer grounded questions about video content, locate when people, actions, dialogue, or scenes occur, and return inspectable keyframes or clips. Trigger for requests such as "find where X appears," "when does Y happen," "what is said," "what happens," or "show me the matching clip," even when the user does not name VidXP. Do not trigger for ingesting new media or ordinary video editing.
---

# Find video evidence with VidXP

Use VidXP retrieval and its returned evidence as one workflow. Do not make the
user translate timestamps into separate clip requests when the ordinary search
can deliver frames and clips itself.

## Workflow

1. Resolve the declared `vidxp` MCP dependency. When the host supports lazy
   connector or tool discovery, search for VidXP before concluding it is absent.
   If its tools still cannot be loaded, say that the VidXP connector is not
   connected and stop instead of silently using unrelated search tools.
2. Call `get_workspace` before searching. Select the requested indexed media ID
   when the user identifies one video; otherwise search the active snapshot.
   If the requested video is not indexed, explain that ingestion or indexing is
   required before retrieval.
3. Choose the operation by the requested outcome:
   - Use `search_moments` to locate or list matching moments.
   - Use `query_video` for a grounded answer that combines retrieved evidence.
4. Request `keyframes_and_clips` when the user asks where something occurs,
   wants verification, or is likely to share/download the result. Use keyframes
   alone only when clips add no value.
5. Poll only the submitted job with `get_job`. The completed job is the
   authoritative source for ranked results, evidence, frames, clips, and
   ResourceLinks.
6. Inspect returned keyframes before asserting that a person or action is
   visibly present. Distinguish visual appearances from dialogue mentions.
7. When useful candidates extend beyond the initial evidence delivery, call
   `materialize_job_evidence` with evidence IDs from the completed job in
   batches of at most ten. Request keyframes for verification and add clips
   only for results worth presenting. Do not rerun retrieval or reconstruct
   timestamps in FFmpeg.

## Current actor scope

- Use `query_video`, not `search_moments`, when anonymous actor-cluster context
  is useful. The actor capability has no text-search operation, so it cannot
  search a person's name or accept a reference identity.
- Treat actor evidence as video-scoped anonymous face clusters containing a
  detection count and first-to-last sampled timestamp range. It does not prove
  a name, continuous presence, speaking identity, or cross-video identity.
- Actor evidence may fall outside the three items materialized automatically.
  Select its evidence IDs from the completed job and use
  `materialize_job_evidence` when a representative frame or clip is useful.
- Label the returned actor image honestly: it is currently a representative
  full frame near the middle of the cluster range, not an exact detection frame,
  face crop, contact sheet, or identity verification.
- Do not claim that MCP can list exact actor detections or create actor overlays.
  Those operations exist in other VidXP interfaces but are not MCP tools yet.

## Polling and presentation

- Explain that search and grounded queries are durable jobs and may take time,
  especially when clips must be rendered.
- Honor a server-provided polling interval when present. Otherwise poll after
  about 5 seconds initially and back off to about 15 seconds for sustained work.
  Never busy-loop or create a shell script merely to wait.
- Reuse the submitted job ID and idempotency key. Give a concise update when the
  job stage changes and approximately once per minute during long unchanged
  work; do not narrate every poll or invent an ETA.
- Evidence is not progressive while the job is running. As soon as the completed
  `get_job` response supplies keyframes, clips, or ResourceLinks, present them to
  the user directly instead of returning only timestamps or waiting for another
  request.
- Include the returned evidence blocks or working resource/download references
  in the final answer. Never leave an evidence heading empty. If the host does
  not render a ResourceLink, say so and use `get_artifact_download` to provide
  the transport-authoritative alternative.
- Stop polling on success, failure, or cancellation. Preserve the job ID and
  report structured remediation when the job does not succeed.

## Evidence rules

- Report the source video, resolved interval, modalities, evidence ID, and
  available frame or clip for each useful result.
- Describe ranking scores as retrieval scores, not calibrated probabilities.
- Treat actor detections and clusters as anonymous until a trusted reference or
  identity registry grounds them. A person's name in the query, dialogue, or
  scene description is not identity proof.
- For named-person searches, verify candidate appearances against each returned
  frame and label uncertain results as candidates. Do not claim an exhaustive
  list of appearances when the available capability cannot establish identity
  across the whole video.
- A failed or empty search means no matching indexed evidence was found; it does
  not prove that the event is absent from the original video.
- If a result has authoritative evidence but no requested clip, prefer
  `create_evidence_clip`; VidXP resolves and clamps its range. Use
  `get_artifact_download` only when the returned ResourceLink is insufficient for
  the host or the user explicitly asks for a path or download link.
- Use external FFmpeg extraction only after VidXP returns a structured failure
  for both follow-up materialization and the evidence-clip fallback, and disclose
  that substitution to the user.
