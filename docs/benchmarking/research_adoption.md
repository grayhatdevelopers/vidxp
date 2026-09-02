# Research adoption record

Collection index: [Benchmarking research](README.md)

Status: Current source of truth

Last verified: 2026-09-02

This page answers one question: **which published ideas are present in VidXP,
where are they present, and why?** The broader
[paper inventory](research_papers.md) tracks relevant work; the
[validation ledger](paper_validation.md) records what was checked. Neither of
those documents implies adoption.

## Status meanings

- **Adopted**: the named method or model is in the current product path.
- **Control**: retained so a replacement can be measured against it; not the
  intended final architecture.
- **Candidate**: relevant and evaluated on paper, but not implemented in VidXP.
- **Not adopted**: reviewed and deliberately not represented as product design.

An approach is not “paper-derived” merely because it resembles a paper after the
fact. A paper-derived change must cite the exact method, state any deviation,
and pass the decision measurements in
[multimodal model direction](model_selection.md#decision-measurements).

## What is adopted now

| Research source | Adopted part | Product location | Why it is used | VidXP-specific deviation or limit |
| --- | --- | --- | --- | --- |
| [FineLAP](https://aclanthology.org/2026.acl-long.473/) | Released language-audio model and its global and dense representations | `src/vidxp/capabilities/sound/` | One checkpoint supplies environmental-sound retrieval and fine-grained activations | VidXP creates ten-second windows and 0.16-second activation records. Cross-window ranking and final intervals are VidXP behavior, not FineLAP's grounding algorithm. |
| [Reciprocal Rank Fusion](https://doi.org/10.1145/1571941.1572114) | Rank-only fusion with the paper's `k = 60` constant | `src/vidxp/search_fusion.py` | Combines uncalibrated modality rankings without training or pretending their similarity scores share a scale | VidXP contributes only the best rank per modality inside a temporal component. The component construction and returned interval are not defined by the RRF paper. |
| [VideoPrism](https://arxiv.org/abs/2402.13217) | Released video encoder checkpoint | `src/vidxp/capabilities/action/` | Supplies motion-aware clip embeddings | VidXP groups 16 samples at 2 fps into non-overlapping records. That eight-second record design is an implementation choice, not a boundary method from VideoPrism. |
| [SigLIP 2](https://arxiv.org/abs/2502.14786) | Released image-text encoder checkpoint | `src/vidxp/capabilities/scene/` | Supplies dense visual-semantic frame retrieval | VidXP samples at 1 fps and stores each sample until the next sample. These records are not semantic scenes despite the capability name. |
| [Whisper](https://arxiv.org/abs/2212.04356) and [Qwen3 Embedding](https://arxiv.org/abs/2506.05176) | Speech-recognition model family and text embedding model | `src/vidxp/capabilities/speech/` | Produces timestamped transcript evidence and semantic transcript retrieval | `faster-whisper` is the runtime implementation. Transcript segmentation, storage, and search are VidXP integration choices. |

## Current behavior with no research-adoption claim

| Behavior | Status | Exact statement |
| --- | --- | --- |
| Fixed VideoPrism records | Control | Sixteen frames at 2 fps form a record of about eight seconds. No paper was adopted to choose this as the correct temporal unit. |
| One-second SigLIP2 records | Control | They provide dense visual evidence, not detected shot or scene boundaries. |
| Connected-interval grouping | Control | Every overlapping hit, including transitive overlap across modalities, enters one component. This is local implementation logic. |
| Component interval union | Control | The returned start is the earliest hit start and the end is the latest hit end. This can let one coarse hit widen otherwise precise evidence. |
| Equal `top_k` retrieval per modality | Control | The same requested depth is passed to each modality before fusion. There is no paper-backed candidate-recall policy yet. |

The reverted `4x` candidate over-fetch and anchor-preserving union experiment is
not adopted. Its multiplier was selected after observing one benchmark case, so
it cannot be cited as a general or research-derived solution.

## Boundary and candidate methods reviewed but not adopted

| Exact work | What the full method does | Evidence and fit | Decision |
| --- | --- | --- | --- |
| [Zero-shot Video Moment Retrieval With Off-the-Shelf Models](https://proceedings.mlr.press/v203/diwan23a.html) (Diwan et al., PMLR 2023) | PySceneDetect proposals, one-fps CLIP scoring, then similarity-threshold watershed merging; reported settings were tuned on QVHighlights `val-filt` | Closest simple frozen-encoder baseline and executable method specification, but the split and thresholds are dataset-specific and no official implementation was found | **Candidate** for a faithfully reproduced zero-shot control, not a production recipe |
| [Zero-Shot Video Moment Retrieval From Frozen Vision-Language Models](https://openaccess.thecvf.com/content/WACV2024/html/Luo_Zero-Shot_Video_Moment_Retrieval_From_Frozen_Vision-Language_Models_WACV_2024_paper.html) (Luo et al., WACV 2024) | Splits compound queries into single-action queries, refines frozen VLM features, clusters each into proposals, and combines overlapping proposal sets | Directly relevant to compound queries. Its `k = 6` clustering and refinement settings were selected on Charades-STA, and no official code was located | **Candidate**; reproduce before borrowing its query decomposition or proposal logic |
| [Training-free Video Temporal Grounding](https://arxiv.org/abs/2408.16219) (Zheng et al., ECCV 2024) | Uses an LLM to decompose and order sub-events, VLM dynamic/static scoring, then filters and integrates proposals | Peer-reviewed with [official code](https://github.com/minghangz/TFVTG) and useful for ordered compound queries; the release uses BLIP2, stored or query-time LLM output, proposal enumeration, and hard-coded CUDA execution | **Candidate** for a compound-query baseline, not a direct macOS or default local path |
| [Anchor-Aware Similarity Cohesion](https://openaccess.thecvf.com/content/CVPR2025/html/Tan_Anchor-Aware_Similarity_Cohesion_in_Target_Frames_Enables_Predicting_Temporal_Moment_CVPR_2025_paper.html) (Tan et al., CVPR 2025) | Trains query-conditioned feature alignment and a 2D boundary detector around the highest-relevance frame | Official code exists and boundary ablations are strong, but it is supervised, visual-only, and uses dataset-specific convolution widths | **Candidate** trained boundary ceiling; unrelated to the reverted custom “anchor” heuristic |
| [Lighthouse](https://aclanthology.org/2024.emnlp-demo.6/) (Nishimura et al., EMNLP 2024) | Reproduces six trained moment/highlight models behind one inference API | Apache-2.0 code, checkpoints, and CPU inference exist; video input is capped at 150 seconds and CPU guidance uses CLIP-only features | **Candidate** executable control surface, especially for QD-DETR; not a new localization algorithm |
| [UniVTG](https://github.com/showlab/UniVTG) (Lin et al., ICCV 2023) | A pretrained temporal head unifies interval, saliency-curve, and point labels | Official MIT code and checkpoints; practical inference claim, but benchmark adaptation remains GPU-oriented and visual-only | **Candidate** established trained interval control |
| [UniversalVTG](https://arxiv.org/abs/2604.08522) (An et al., arXiv 2026) | Cross-dataset pretraining, offline query canonicalization, and a lightweight grounding head | Official checkpoint/API exists, but evaluation and feature extraction require CUDA and its upstream encoder has a separate Meta/Fair license | **Candidate**, too new and not currently Mac-runnable end to end |
| [REZE](https://arxiv.org/abs/2608.04480) (Li et al., arXiv 2026) | Scores consecutive three-second clips with a frozen VLM, then applies deterministic smoothing and interval extraction outside the model | Directly isolates recognition from boundary extraction and reports full score/aggregation ablations. It requires many 7B/8B VLM clip calls and is a four-week-old preprint with no public code found | **Candidate** high-value research reproduction; not established enough for direct adoption |
| [STITCH](https://arxiv.org/abs/2608.27929) (Casanova et al., arXiv 2026) | Builds reusable query-independent chunks by change-point detection over frozen InternVideo2 windows, then scores chunks per query | Closest published match to VidXP's reusable-index constraint. It is days old, submitted rather than accepted, uses an anonymized artifact, and was evaluated on a CUDA GPU | **Candidate** for a bounded temporal-unit experiment after artifact review |
| [Point-to-Span](https://arxiv.org/abs/2512.10363) and [GranAlign](https://arxiv.org/abs/2601.00584) | P2S expands similarity peaks adaptively and refines with ordered subqueries; GranAlign rewrites queries and generates query-aware captions at two semantic granularities | Both address real zero-shot failure modes and publish ablations. Both add query-time model work; no official public code was found in the checked paper surfaces | **Candidates** for long-video and semantic-granularity comparisons, not implementation instructions |
| [NumPro](https://openaccess.thecvf.com/content/CVPR2025/html/Wu_Number_it_Temporal_Grounding_Videos_like_Flipping_Manga_CVPR_2025_paper.html) and [Moment-GPT](https://arxiv.org/abs/2501.07972) | NumPro overlays frame numbers for a video LLM; Moment-GPT rewrites queries, generates spans, and uses multiple frozen MLLMs to score them | Both target direct MLLM timestamping. They alter media or add heavy query-time inference and do not use VidXP's indexed multimodal evidence | **Not selected** for the first product experiment |

## Verified failure and next comparison

The saved post-FineLAP-fix development run ranks the correct opening region
first in action, scene, and sound. Its 0–8.0075-second output is wider than the
0–6-second reference because the connected-component union preserves the full
eight-second action record. The earlier random sound result predates commit
`343bd27` and must not be used to diagnose current ranking.

This evidence narrows the next work to interval localization; it does not
support replacing the encoders, indexes, or product architecture. Both named
methods require a dense similarity sequence, which the saved `top_k = 3` search
result does not contain. The first comparison must therefore export the full
per-frame curve for the same prepared LongVALE media and queries, then measure:

- current RRF-ranked connected-component union;
- Diwan et al.'s 2023 zero-shot proposal, matching, and post-processing method;
  and
- TFVTG's ECCV 2024 dynamic/static proposal scoring and ordered sub-event
  integration.

Paper-encoder reproductions and VidXP-encoder adaptations are different
experiments. The latter can isolate interval logic without adding a production
model, but it must not be reported as a paper-faithful TFVTG or Diwan result.

RRF remains the coarse ranker in the VidXP control. Neither its paper nor the
two localization papers justify an arbitrary candidate multiplier. Retrieval
depth and final output count must be measured separately and recorded as an
original VidXP execution choice unless a subsequently adopted method defines
them. REZE and STITCH remain later research candidates, not the immediate
implementation direction.

## Required record for future adoption

Every paper-derived product change must update this page with:

1. exact paper, version, venue, and artifact revision;
2. method component adopted and code location;
3. deviations from the published method;
4. benchmark and resource evidence that justified adoption; and
5. rejected alternatives and the reason they lost.

If a change is original VidXP engineering, label it as such and record the
evidence. Do not attach a paper citation retroactively.
