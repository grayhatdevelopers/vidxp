---
name: vidxp-find-video-evidence
description: Use VidXP to search indexed videos, answer grounded questions about video content, locate when people, actions, dialogue, or scenes occur, and return inspectable evidence boards, keyframes, or clips. Trigger for requests such as "find where X appears," "when does Y happen," "what is said," "what happens," or "show me the matching clip," even when the user does not name VidXP. Do not trigger for ingesting new media or ordinary video editing.
---

# Find video evidence with VidXP

## Workflow

1. Resolve the `vidxp` MCP tools, then call `get_workspace`. If the requested
   video is not indexed, explain that it must be indexed first.
2. Use `search_moments` to locate moments or `query_video` for a synthesized,
   grounded answer. Set `command.media_id` when the user means one video.
3. Omit `command.evidence_delivery` for the normal path. The completed job
   includes an annotated board covering the ranked results.
4. Poll only that job with `get_job`, honoring `poll_after_seconds`. Search and
   query may take time; update the user when the stage changes or about once per
   minute, never on every poll and never with an invented ETA.
5. Inspect and show the returned board before making visual claims. Use its tile
   evidence IDs for follow-up:
   - `materialize_job_evidence` accepts up to ten selected IDs and returns
     standalone keyframes or clips without rerunning retrieval.
   - `create_evidence_board` is only for a custom selection or the
     `next_start_rank` continuation; poll its returned job ID.
6. When standalone artifacts are required in the initial job, put exactly this
   inside `command`: `"evidence_delivery": {"mode":
   "keyframes_and_clips", "max_items": 3}`. Never send
   `command.materialize`.

## Actor scope

- Actor data is available through `query_video`, not name search. It represents
  anonymous, video-scoped face clusters—not a named or cross-video identity.
- Treat its image as a representative full frame, not an exact face crop or
  proof of continuous presence. Do not claim exhaustive named appearances.
- Use scene evidence plus board inspection for named-person requests, and label
  uncertain matches as candidates.

## Output

- Present returned board images, frames, clips, or working resource links—not
  timestamps alone. If a host does not render a link, use
  `get_artifact_download`.
- Preserve the source job and evidence IDs. Describe scores as retrieval scores,
  and distinguish a visible appearance from a dialogue or caption mention.
- Stop polling on success, failure, or cancellation. An empty result means no
  matching indexed evidence was found, not that the event is absent from the
  original video.
