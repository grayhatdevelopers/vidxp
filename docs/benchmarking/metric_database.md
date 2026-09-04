# VidXP metric database

Last verified: 2026-09-04

This is the public index of VidXP's measured results. Each result identifies the
research protocol or method it tests, VidXP's deviation from that work, the
machine used, and the conclusion the evidence supports. The tables contain the
relevant measurements; they do not depend on one maintainer's local files.

Use [published comparison results](published_results.md) for other systems'
reported scores and [research adoption](research_adoption.md) for the smaller
list of ideas accepted into VidXP. Scores below use proportions from `0` to `1`
unless a percent sign is shown.

## Machines used

| ID | Hardware | Software and execution | Applies to |
| --- | --- | --- | --- |
| `mac-m2-01` | MacBook Pro `Mac14,7`; Apple M2; 8 CPU cores (4 performance, 4 efficiency); 10 GPU cores; 8 GB memory; ARM64 | macOS 15.6.1 (`24G90`); Python 3.14.7; PyTorch 2.13.0; Transformers 5.14.1; ChromaDB 1.5.9; NumPy 2.5.1; FFmpeg 8.1.1; Node.js 22.23.2; Promptfoo 0.122.2. VidXP selected CPU; PyTorch reported neither MPS nor CUDA available. | September 2026 agent and component rows. The profile was captured on September 4; the older artifacts do not embed their own machine snapshot, so this assignment is retrospective. |
| `win-hp-01` | HP ENVY Laptop 16-h0xxx; Intel Core i7-12700H; 14 cores, 20 logical processors; 15.72 GiB memory; NVIDIA RTX 3060 Laptop GPU with 4 GiB VRAM | Windows 11; Python 3.14.0; PyTorch 2.13.0+cpu; Transformers 5.14.1; Sentence Transformers 5.6.1; ChromaDB 1.5.9. The GPU was present but unused. | July 2026 official-adapter rows. Current-provider manifests contain this snapshot; surviving legacy artifacts do not contain every package or immutable model revision. |

## System evaluated

| Evidence | Provider and revision | Product representation | Research boundary |
| --- | --- | --- | --- |
| Speech | faster-whisper `large-v3-turbo@0a363e9` and Qwen3 Embedding `0.6B@97b0c61` | Timestamped transcript segments | [Whisper](https://arxiv.org/abs/2212.04356) supplies transcription and [Qwen3 Embedding](https://arxiv.org/abs/2506.05176) supplies semantic retrieval. Benchmarks that provide transcripts do not test transcription. |
| Scene | SigLIP 2 `base-patch16-224@75de2d5` | Frames sampled at 1 fps | [SigLIP 2](https://arxiv.org/abs/2502.14786) supplies image-text similarity. It does not predict scene or event boundaries. |
| Action | VideoPrism `lvt-base-f16r288@fb6de9f` | Sixteen-frame clips sampled at 2 fps, normally about eight seconds | [VideoPrism](https://arxiv.org/abs/2402.13217) supplies global video-text embeddings. VidXP's fixed windows and raw long-video ranking are not the paper's action-localization method. |
| Sound | FineLAP `b419aa2` | Ten-second global windows and 0.16-second dense activations | [FineLAP](https://aclanthology.org/2026.acl-long.473/) trains separate global and local projections. VidXP's global-then-local search is its own long-video orchestration. |
| Fusion | No model | Overlap-connected evidence groups ranked with RRF; group interval is the union of its records | [RRF](https://doi.org/10.1145/1571941.1572114) defines `sum(1 / (60 + rank))`. Temporal grouping, one rank per modality, and interval union are VidXP rules. |

Full immutable revisions are pinned in the
[speech](../../src/vidxp/capabilities/speech/specs.py),
[scene](../../src/vidxp/capabilities/scene/specs.py),
[action](../../src/vidxp/capabilities/action/specs.py), and
[sound](../../src/vidxp/capabilities/sound/specs.py) specifications. A row below
states when an experiment replaces these normal representations.

## Whole-system agent measurements

These paired runs use one [LongVALE](https://openaccess.thecvf.com/content/CVPR2025/papers/Geng_LongVALE_Vision-Audio-Language-Event_Benchmark_Towards_Time-Aware_Omni-Modal_Perception_of_Long_Videos_CVPR_2025_paper.pdf)-derived
development task with reference interval `0–6` seconds. They compare the same
Codex model with VidXP MCP evidence and with direct media inspection. They prove
the harness and expose product behavior; one task is not a LongVALE score or a
held-out quality estimate.

| Evaluation | Machine | VidXP-on | Direct inspection | Efficiency comparison | Valid conclusion |
| --- | --- | --- | --- | --- | --- |
| `eval-J6s-2026-09-01T19:30:07` | `mac-m2-01` | `0–8.0075` s; IoU `0.7493`; 74.552 s; 301,712 total tokens; 48,423 uncached input; 1,769 output; 6 MCP calls; $0.815355 provider estimate | `0–6.8` s; IoU `0.8824`; 112.209 s; 329,961 total tokens; 35,906 uncached input; 3,623 output; 10 media shell calls; $0.812527 estimate | VidXP used 28,249 fewer tokens and 37.657 fewer seconds, but more uncached input made its estimate $0.002828 higher. | Both found the event. VidXP's connected union adopted the eight-second action endpoint. This is the valid development harness smoke. |
| `eval-mw5-2026-09-02T19:40:44` | `mac-m2-01` | `0–10` s; IoU `0.6000`; 79.647 s; 261,995 total tokens; 48,523 uncached input; 1,760 output; 7 tools, including 6 MCP calls; $0.401271 estimate | `0–6.81` s; IoU `0.8811`; 89.757 s; 313,617 total tokens; 56,950 uncached input; 3,227 output; 9 media shell calls; $0.968155 estimate | VidXP used 51,622 fewer tokens, 10.110 fewer seconds, two fewer tools, and a $0.566884 lower estimate. | Superseded global-only FineLAP diagnostic. The ten-second result rejects a global sound window as the final boundary; it does not measure current two-stage sound search. |

Cost is the provider-reported estimate. Cached and uncached input can have
different rates, so total tokens alone do not determine it. Reasoning tokens are
already included in output tokens.

## Component and ranking measurements

These controls use frozen LongVALE-derived tasks and `mac-m2-01`. They make no
Codex or API calls. “Candidate recall” asks whether a usable interval exists in
the returned list; it does not mean that VidXP selected that interval.

| Experiment and research basis | Scope and cost | Result | What it establishes |
| --- | --- | --- | --- |
| `p2s_asg_vidxp_v1`; [Point-to-Span](https://arxiv.org/abs/2512.10363), Section 3.1 | One development task; saved score curves; no model calls | Current union IoU `0.7493`; adapted interval `0.64–6.72` s and IoU `0.7976`; direct-inspection IoU `0.8824` | The adaptive sound span helped, but the partial adaptation produced no scene or action span and remained below direct inspection. It is concluded, not adopted. |
| `videoprism_overlap_control_v1`; [CTAP](https://openaccess.thecvf.com/content_ECCV_2018/html/Jiyang_Gao_CTAP_Complementary_Temporal_ECCV_2018_paper.html) and [long-video guidance](https://openaccess.thecvf.com/content/ICCV2023/html/Barrios_Localizing_Moments_in_Long_Video_Via_Multimodal_Guidance_ICCV_2023_paper.html) motivate candidate coverage | Five action tasks; normal 79 records versus 307 four-second records; five text embeddings; fine index took 1,175.579 s and wrote 5,966,316 bytes | Eight-second top-1 mean IoU `0.0680`, R@1 at tIoU 0.5 `0`; four-second top-1 mean IoU `0.1297`, R@1 at tIoU 0.5 `.20`, top-3 candidate recall `.40`, full-list recall `.60`; top-three coarse gating reduced full-list recall to `.40` | Overlap improves candidate availability, but raw VideoPrism similarity and the tested gate do not rank it reliably. CTAP's learned proposal ranking and boundary adjustment were not implemented. |
| `diwan_shotdetect_siglip2_v1`; [Off-the-Shelf VMR](https://proceedings.mlr.press/v203/diwan23a.html) | Eight tasks; 16 text embeddings; 46.744 s probe generation; 16.923 s shot detection; no model calls or index writes for detection | Development shot IoU `0.8902`. Held out: current union mean IoU `0.0418`; best-shot oracle `.5219`; on six scene-comparable tasks, scene ranking `.2841` versus proposal RRF `.1175` | Shot boundaries can supply useful candidates. VidXP's proposal RRF harmed ranking; five tasks were boundary-limited and three ranking-limited at tIoU 0.5. The paper's CLIP plus SimpleWatershed pipeline was not reproduced. |
| `manual_modality_query_ceiling_v1`; query decomposition is compared with, not claimed from, [Zero-Shot VMR](https://openaccess.thecvf.com/content/WACV2024/html/Luo_Zero-Shot_Video_Moment_Retrieval_From_Frozen_Vision-Language_Models_WACV_2024_paper.html) | Eight tasks; 16 task-modality pairs; 32 text embeddings; 13.590 s | Target overlap in top 3 changed `7/16` to `8/16`; best-boundary record in top 3 changed `4/16` to `7/16`; nine overlap ranks improved and two worsened | Manual modality wording is an upper-bound control, not the paper's full method. Mixed results reject mandatory rewriting. |
| `finelap_separate_streams_v1`; [FineLAP](https://aclanthology.org/2026.acl-long.473/), Sections 3.2–3.3 | Four sound tasks within the preceding query control | A target appeared in a top-three list on `0/4` tasks when global and dense records were mixed and `3/4` when the streams were ranked separately | The original mixed ranking was invalid. The result supports separate representation paths, not VidXP's final global-then-local selector. |
| `finelap_two_stage_runtime_2026-09-03`; [FineLAP](https://aclanthology.org/2026.acl-long.473/) representations with VidXP orchestration | One real Apple Silicon query through FineLAP, Chroma, the application, fusion, and JSON output | Dense result `1.60–2.08` s with parent context `0–10` s | Real-path smoke only. It proves the current selector executes; it supplies no held-out IoU or comparative quality evidence. |
| `finelap-two-stage-held-out@eae7000`; [FineLAP](https://aclanthology.org/2026.acl-long.473/), Sections 3.2–3.3, plus VidXP's selector | Four held-out sound tasks; full frozen application queries; top 3; eight local text embeddings including diagnostic duplication; 5.172 s total | Global gate coverage `2/4`; final activation top-1 and top-3 coverage `0/4`; full gated activation coverage `2/4`; final mean IoU `0`; R@1 at tIoU 0.3/0.5/0.7 all `0`; surviving target ranks `132` and `63` | Selector rejected. FineLAP validates separate clip and frame outputs, not VidXP's long-video gate or pooled cross-window activation ranking. Do not spend an agent run on this path. |
| `videoprism_provider_conformance_2026-09-03`; [VideoPrism](https://arxiv.org/abs/2402.13217) official preprocessing and checkpoint | One identical 16-frame tensor and six texts through official Flax and pinned Transformers implementations | Video and text embedding cosine parity rounded to `1.0`; every similarity score differed by less than `0.000051` | The port is numerically valid. Query canonicalization was fixed in `7e7d6c8`; the remaining failure is VidXP's global-similarity ranking design. |

The action result motivates a trained sequence-to-interval grounder rather than
more fixed-window tuning. [HieraMamba](https://openaccess.thecvf.com/content/CVPR2026/html/An_HieraMamba_Video_Temporal_Grounding_via_Hierarchical_Anchor-Mamba_Pooling_CVPR_2026_paper.html)
establishes hierarchical long-video grounding, and
[UniversalVTG](https://arxiv.org/abs/2604.08522) applies it through one
cross-domain checkpoint. Neither release currently satisfies VidXP's CPU/Mac
and licensing gates; this is a documented direction, not a VidXP result.

## Official adapter measurements

These rows test dataset adapters and current or legacy providers. Smoke subsets
validate execution and evaluator compatibility, not provider quality.

| Benchmark and research protocol | Machine | Scope | Result | Evidence status |
| --- | --- | --- | --- | --- |
| [DiDeMo](https://openaccess.thecvf.com/content_iccv_2017/html/Hendricks_Localizing_Moments_in_ICCV_2017_paper.html) | `win-hp-01` | Official test; 4,021 searches over 1,037 videos; legacy CLIP provider | Rank@1 `20.19%`; Rank@5 `55.71%`; mean IoU `34.60%` | Full legacy result. One corrupt official media object was replaced with a byte-matching archived copy, as recorded in [adapter validation](adapter_validation.md). |
| [HiREST](https://openaccess.thecvf.com/content/CVPR2023/papers/Zala_Hierarchical_Video-Moment_Retrieval_and_Step-Captioning_CVPR_2023_paper.pdf) | `win-hp-01` | Official validation; 193 known-video searches; released transcripts and legacy MiniLM | R@0.5 `78.24%`; R@0.7 `44.56%` | Full validation result, not a held-out test score and not a transcription result. |
| [DiDeMo](https://openaccess.thecvf.com/content_iccv_2017/html/Hendricks_Localizing_Moments_in_ICCV_2017_paper.html) current-provider smoke | `win-hp-01` | Official test annotation index `0`; one video; current SigLIP 2 | Rank@1 `0`; Rank@5 `1`; mean IoU `0` | One-example real provider, storage, serialization, and official-evaluator check only. |
| [HiREST](https://openaccess.thecvf.com/content/CVPR2023/papers/Zala_Hierarchical_Video-Moment_Retrieval_and_Step-Captioning_CVPR_2023_paper.pdf) current-provider smoke | `win-hp-01` | Two declared validation pairs over two videos; released transcripts and current Qwen3 | R@0.5 `.50`; R@0.7 `.50` | Two-example real provider, storage, filtering, serialization, and evaluator check only. |

## Evidence retained in the repository

The tables above are the public numeric record. The repository also retains the
inputs and code needed to understand or reproduce them:

- the [LongVALE-derived task manifest](../../benchmarks/codex-mcp/tasks/longvale-part9-pilot.json),
  [fixed agent prompt](../../benchmarks/codex-mcp/prompts/video-evidence.txt),
  [Promptfoo configuration](../../benchmarks/codex-mcp/promptfooconfig.yaml),
  and [reporter](../../benchmarks/codex-mcp/scripts/report.mjs);
- the action, proposal, query, sound, and Point-to-Span controls under
  `benchmarks/codex-mcp/scripts/`;
- [current result interpretation](results.md), [paper validation](paper_validation.md),
  and [published comparison results](published_results.md).

Generated databases, predictions, media, indexes, and model weights are not
committed. A raw artifact export can be specified separately; machine-specific
paths are not part of this public evidence record.

## Measurements still required

- Replace or remove the rejected FineLAP two-stage selector before another
  paired agent run.
- Run the 54-run paired Codex pilot only after explicit maintainer approval.
- Produce full-corpus DiDeMo and HiREST results for the current providers.
- Add Git revision, machine snapshot, model revisions, task-manifest hash, wall
  time, peak memory, model-call counts, agent/API usage, and raw-prediction
  identity to future generated run manifests. Do not infer missing historical
  fields.
