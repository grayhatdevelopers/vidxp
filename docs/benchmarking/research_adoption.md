# Research adoption record

Collection index: [Benchmarking research](README.md)

Status: Current source of truth

Last verified: 2026-09-04

This page records which published ideas are in VidXP, where they are used, and
where VidXP deviates. The [paper inventory](research_papers.md) and
[validation ledger](paper_validation.md) cover reviewed work that is not
adopted.

## Status meanings

- **Adopted**: used in the current product path.
- **Control**: current behavior retained for comparison, without a claim that
  it is the final design.
- **Experiment**: benchmark-only code, not product behavior.
- **Candidate**: reviewed but neither adopted nor implemented.

A similar-looking implementation is not paper-derived after the fact. Every
paper-derived change must name the exact source and method, document deviations,
and record the evidence used to accept it. Original VidXP engineering must be
labeled as such.

## Product adoptions

| Source | Adopted part and location | Reason | VidXP deviation or limit |
| --- | --- | --- | --- |
| Li et al., [FineLAP](https://aclanthology.org/2026.acl-long.473/), ACL 2026, Sections 3.2–3.3 | Released global and local audio representations in `src/vidxp/capabilities/sound/` | Supplies environmental-sound retrieval and timestamped activation features | FineLAP evaluates clip captions globally and event phrases against frame labels locally. Its Limitations section excludes long-form retrieval. VidXP's current selector failed held-out validation and is not an adopted research method. |
| Cormack, Clarke, and Buettcher, [Reciprocal Rank Fusion](https://doi.org/10.1145/1571941.1572114), SIGIR 2009 | Rank-only formula with `k = 60` in `src/vidxp/search_fusion.py` | Combines modality rankings without treating their raw distances as one scale | Connected temporal grouping, one best rank per modality, and interval union are VidXP controls, not parts of the paper. |
| Zhao et al., [VideoPrism](https://arxiv.org/abs/2402.13217), ICML 2024, and Google's public LvT checkpoint | Global video-text embeddings and official text canonicalization in `src/vidxp/capabilities/action/` | Supplies cross-modal similarity for short action clips | VidXP's fixed windows and long-video ranking are not VideoPrism methods. The paper's action results use task-specific evaluation heads and do not validate raw similarity as temporal action localization. |
| Tschannen et al., [SigLIP 2](https://arxiv.org/abs/2502.14786), 2025 | Released image-text encoder in `src/vidxp/capabilities/scene/` | Supplies visual-semantic frame retrieval | VidXP samples at 1 fps. These records are sampled frames, not detected semantic scenes. |
| Radford et al., [Whisper](https://arxiv.org/abs/2212.04356), ICML 2023, and Zhang et al., [Qwen3 Embedding](https://arxiv.org/abs/2506.05176), 2025 | Speech recognition and text embeddings in `src/vidxp/capabilities/speech/` | Produces timestamped, searchable transcript evidence | `faster-whisper` is the runtime implementation. Segmentation, storage, and retrieval are VidXP choices. |

Reverting the rejected selector does not require an index rebuild. Replacing
FineLAP with a long-audio model uses different features and does require one.

## Sound replacement decision

The product request is a free-form query over a video's full audio track. The
matching research task is **audio moment retrieval**, not clip retrieval and not
event-label sound detection.

| Candidate | Grounded result | Product decision |
| --- | --- | --- |
| Official DCASE 2026 MS-CLAP/QD-DETR baseline | Directly predicts intervals from one-second audio features; 13.56 R1@0.7 on the hidden evaluation; MIT code documents CPU inference | First reproducible control, not the quality target |
| M2D-CLAP + modified CG-DETR, Kibata et al. | 48.59 R1@0.7 with 211.87M total parameters, tied first in DCASE 2026 | Best size/quality target found; blocked on unverified public code and weights |
| CASTELLA-trained UVCOM in Lighthouse | Released code and checkpoint; 20.3 R1@0.7 on CASTELLA; supports up to 300-second audio | Executable fallback for a clean Mac compatibility check; known weakness on sub-ten-second moments |
| DASM, FlexSED, WSTAG, and PE-A-Frame | Event-phrase or short-audio grounding systems rather than the full-query long-audio task | Keep as short-event comparators; do not silently substitute them for the default query path |

The next product change is not another FineLAP gate. First verify whether the
winning CG-DETR checkpoint is obtainable under a usable license. If it is not,
port the released CASTELLA/Lighthouse path as an isolated provider and compare it
with the official DCASE baseline on the frozen sound tasks. Do not add a learned
model to the default path until it beats the current control and its runtime fits
the 8 GB CPU machine.

## Original product controls

| Behavior | Exact status |
| --- | --- |
| Fixed VideoPrism records | Sixteen frames sampled at 2 fps form a record of about eight seconds. No paper was adopted to select this temporal unit. |
| Raw VideoPrism similarity ranking | Global LvT cosine similarity ranks the fixed records. This is a product control, not the action-localization method evaluated in the paper. |
| One-second SigLIP 2 records | They provide dense visual evidence, not shot or scene boundaries. |
| FineLAP two-stage search | Current code gates on three global records, pools their local records, and returns the top three local records. On four held-out sound tasks, the gate covered `2/4` targets and the returned local records covered `0/4`; this original VidXP control is rejected. |
| Connected-interval grouping | Every overlapping hit, including transitive overlaps, enters one component. This is VidXP logic. |
| Component interval union | A component starts at its earliest hit and ends at its latest. It is a coarse evidence envelope and can be widened by one record. |
| Equal `top_k` per modality | Each modality receives the requested retrieval depth. There is no adopted candidate-allocation method. |
| Optional query model | A language model may plan searches or summarize citable evidence. Model size and reasoning are deployment choices, not research adoptions. |

The reverted `4x` over-fetch and anchor-preserving union rule is not adopted. Its
multiplier was selected after one development example and has no general claim.

## Benchmark-only experiments

| ID | Source and scope | Recorded result | Decision |
| --- | --- | --- | --- |
| `p2s_asg_vidxp_v1` | Point-to-Span v1, Section 3.1 only; VidXP score curves and early NMS replace the unreproduced full pipeline | Development IoU changed from `0.7493` to `0.7976`; only sound produced a span, below the direct-inspection agent's `0.8824` | Concluded diagnostic; not adopted |
| `videoprism_overlap_control_v1` | CTAP/Barrios et al. motivate overlapping windows; VidXP replaced the normal action index with four-second windows at a two-second stride | On five held-out action tasks, full-list candidate recall at tIoU 0.5 rose from `0.20` to `0.60` and top-1 recall from `0.00` to `0.20`; a top-three coarse gate reduced candidate recall to `0.40` | Overlapping records remain useful candidates. Current union and the tested coarse gate are rejected; no product selector is adopted |
| `diwan_shotdetect_siglip2_v1` | Diwan et al. ShotDetect proposals, scored with existing SigLIP 2 records; VidXP added proposal-level RRF | Development IoU reached `0.8902`; on six scene-comparable held-out tasks RRF reduced mean IoU from `0.2841` to `0.1175` | Proposal-level RRF rejected; code retained as a control |
| `manual_modality_query_ceiling_v1` | Luo et al. and TFVTG motivate decomposition, but manual modality wording is a VidXP ceiling rather than either published method | Top-three target coverage changed from 7/16 to 8/16; nine ranks improved and two worsened | Mandatory rewriting rejected |
| `finelap_separate_streams_v1` | FineLAP Sections 3.2–3.3; global windows and dense activations queried separately | Top-three target coverage changed from 0/4 mixed to 3/4 across separate lists | Supports the product rule not to cross-rank the raw outputs; no local-activation product surface selected |
| `finelap-two-stage-held-out` | FineLAP's two representations with VidXP's global top-three gate and pooled local ranking | Gate coverage `2/4`; final top-three coverage `0/4`; mean final IoU `0` | Selector rejected; no paired agent run |

The experiment code lives in `src/vidxp/benchmarks/` and
`benchmarks/codex-mcp/scripts/`. Frozen settings and task data remain beside the
scripts. These controls may be reproduced, but they are not a queue of product
changes.

## Confirmed conclusions

- The saved development run found the correct opening region. Its
  `0–8.0075`-second result was wider than the `0–6` reference because the
  eight-second action record set the component endpoint.
- The overlapping-action control exposed a finer near-target record. It tested
  both a replacement index and, in the held-out follow-up, an
  eight-second-to-four-second search path. Fine candidate availability improved,
  but the coarse gate missed one viable region and similarity ranking usually
  did not select the best fine record.
- The pinned Transformers port matches Google's official Flax checkpoint on an
  identical 16-frame input: video and text embedding cosine parity rounded to
  `1.0`, and all six checked similarity scores differed by less than `0.000051`.
  The port is not the observed ranking failure. VidXP did omit the official
  query canonicalization; that provider-contract bug is corrected in the
  action search path. On the five held-out action tasks, the correction left
  mean top-1 IoU at `0.1297` and did not improve any threshold rate; it is a
  conformance fix, not the ranking solution.
- FineLAP's global and local records cannot be treated as one raw-distance
  ranking. Current sound search uses a global gate followed by local activations,
  but that selector failed and must not be described as adopted behavior.
- RRF is useful as a transparent ranking control, but the current temporal
  grouping and union do not provide exact boundaries.
- The action replacement must consume a temporal feature sequence and predict
  intervals. Another global clip-similarity model does not address the measured
  failure. HieraMamba and UniversalVTG directly study this design, but their
  released CUDA/Mamba runtime and unresolved repository licensing prevent a
  current CPU product adoption.
- The next approved agent comparison should test whether VidXP supplies enough
  evidence for a similarly grounded answer with fewer tokens, less time, or
  fewer media-inspection calls. IoU remains one diagnostic within that result.

## Required record for future adoption

For every paper-derived product change, record:

1. the exact paper, version, venue, and artifact revision;
2. the adopted method component and product code location;
3. every deviation from the published method;
4. quality and resource evidence supporting the decision; and
5. rejected alternatives and why they lost.

For original VidXP engineering, state that it is original and record the same
decision evidence. Do not attach a paper citation retroactively.
