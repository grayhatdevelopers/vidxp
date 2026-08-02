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

## Evidence rules

- Report the source video, resolved interval, modalities, evidence ID, and
  available frame or clip for each useful result.
- Describe ranking scores as retrieval scores, not calibrated probabilities.
- Do not assign a real identity to an anonymous actor cluster without grounded
  identity evidence. Do not claim an exhaustive list of a named person's
  appearances when the available capability cannot establish that identity.
- A failed or empty search means no matching indexed evidence was found; it does
  not prove that the event is absent from the original video.
- If a result has authoritative evidence but no requested clip, prefer
  `create_evidence_clip`; VidXP resolves and clamps its range. Use
  `get_artifact_download` only when the returned ResourceLink is insufficient for
  the host or the user explicitly asks for a path or download link.
