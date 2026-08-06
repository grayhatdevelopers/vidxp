---
name: vidxp-find-video-evidence
description: Use VidXP to search indexed videos and surface inspectable evidence boards, keyframes, and clips before analysis. Trigger for requests such as "find where X appears," "when does Y happen," "what is said," "what happens," or "show me the matching clip," even when the user does not name VidXP. Favor one-pass evidence delivery and only add brief accuracy feedback; do not trigger for ingesting new media or ordinary video editing.
---

# Find video evidence with VidXP

## Workflow

1. Resolve the `vidxp` MCP tools, then call `get_workspace`. If the requested
   video is not indexed, explain that it must be indexed first.
2. Submit one retrieval job. Use `search_moments` to locate moments; use
   `query_video` only when the user asks for a synthesized answer. Use
   `command.query` with `search_moments` and `command.question` with
   `query_video`. Set `command.media_id` when the user means one video.
3. In that initial job, put exactly this inside `command`:
   `"evidence_delivery": {"mode": "keyframes_and_clips", "max_items": 3}`.
   This prepares the ranked board, standalone keyframes, and clips without a
   second retrieval pass. Never send `command.materialize`.
4. Call `wait_job` for bounded waits. Pass its `observation_token` as
   `after_observation_token` on the next wait. When terminal, call
   `get_job_evidence` once. It returns the concise evidence index and visual
   content without the full structured job dump. Search and query may take
   time; update the user when the stage changes or about once per minute, never
   after every wait and never with an invented ETA.
5. Surface the returned board, keyframes, and clips immediately. Do not call
   `get_job`, repeat the search, materialize more evidence, create another
   board, or perform a self-directed verification loop before showing the
   initial evidence.
6. Stop after the first evidence delivery. Only when the user explicitly asks
   for another selection or format, use tile evidence IDs with
   `materialize_job_evidence`, or use `create_evidence_board` for a custom
   selection or `next_start_rank` continuation.

## Actor scope

- Actor data is available through `query_video`, not name search. It represents
  anonymous, video-scoped face clusters—not a named or cross-video identity.
- Treat its image as a representative full frame, not an exact face crop or
  proof of continuous presence. Do not claim exhaustive named appearances.
- For named-person requests, surface the best scene candidates immediately and
  label uncertain matches as candidates. Do not delay delivery while trying to
  prove identity through additional searches.

## Output

- Lead with evidence, not a search narrative: first embed the returned board or
  frame or provide its working resource link, then list the ready clips and
  keyframes. Use the returned `local_path` or `download_url`; never write an
  unlinked label such as “View evidence board.” Use `get_artifact_download`
  only if neither is returned.
- After the evidence, add at most a brief accuracy note. State uncertainty or
  visible mismatches without launching another search. Accuracy feedback must
  not replace or precede the evidence.
- Preserve the source job and evidence IDs. Describe scores as retrieval scores,
  and distinguish a visible appearance from a dialogue or caption mention.
- Stop waiting on success, failure, or cancellation. An empty result means no
  matching indexed evidence was found, not that the event is absent from the
  original video.
