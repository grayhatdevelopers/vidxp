---
name: vidxp-find-video-evidence
description: Use VidXP to search indexed videos and surface inspectable evidence boards, keyframes, and clips before analysis. Trigger for requests such as "find where X appears," "when does Y happen," "what is said," "what happens," or "show me the matching clip," even when the user does not name VidXP. Favor one-pass evidence delivery and only add brief accuracy feedback; do not trigger for ingesting new media or ordinary video editing.
---

# Find video evidence with VidXP

## Retrieve evidence

- Resolve the indexed video and scope retrieval with its `media_id` when the
  user means one video. `get_workspace` returns that ID with the matching media;
  do not repeat the lookup with `list_media`.
- If tool schemas are deferred, resolve only `get_workspace`, the chosen search
  tool, `wait_job`, and `get_job_evidence`; do not enumerate the full catalog.
- Use `search_moments` to locate events and `query_video` for a synthesized
  answer. Use a fresh idempotency key for each new retrieval; reuse a key only
  when retrying that same submission.
- When standalone evidence is useful, request only as many
  `keyframes_and_clips` items as the user needs, capped at three. Wait for the
  job to finish, then use `get_job_evidence` once. It returns the ranked
  intervals, contributing modality spans, board, and artifact links needed for
  normal evidence delivery; fetch the full job record only when the user needs
  machine-readable job details. Carry the returned observation token between
  waits.
- Ground the answer in the initial ranked evidence. Inspect its returned board,
  keyframe, or clip when that can resolve a visible mismatch or uncertainty; do
  not start another retrieval or materialize variants solely to reconfirm an
  already supported result.

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
- When distinct returned moments remain plausible, provide up to three in ranked
  order. Return fewer rather than padding the answer with weak or duplicate
  matches.
- Preserve the source job and evidence IDs. Describe scores as retrieval scores,
  and distinguish a visible appearance from a dialogue or caption mention.
- An empty result means no matching indexed evidence was found, not that the
  event is absent from the original video.
