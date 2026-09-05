# Research adoption record

Collection index: [Benchmarking research](README.md)

Status: Current source of truth

Last verified: 2026-09-05

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
- **Rejected**: executed and failed a stated product gate.
- **Blocked**: not executed because its artifacts, license, or deployment path
  failed first.

A similar-looking implementation is not paper-derived after the fact. Every
paper-derived change must name the exact source and method, document deviations,
and record the evidence used to accept it. Original VidXP engineering must be
labeled as such.

## Product adoptions

| Source | Adopted part and location | Reason | VidXP deviation or limit |
| --- | --- | --- | --- |
| Vyas et al., [PE-AV and PE-A-Frame](https://arxiv.org/abs/2512.19687), CVPR 2026 | PE-A-Frame Small's released audio-frame and text embeddings in `src/vidxp/capabilities/sound/` | Supplies free-form sound-event ranking at the model's 40 ms frame rate | The paper establishes the model and dot-product scoring, not long-media chunking or user-facing intervals. VidXP adds bounded overlapping inference and fixed evidence windows as documented deployment controls. |
| Cormack, Clarke, and Buettcher, [Reciprocal Rank Fusion](https://doi.org/10.1145/1571941.1572114), SIGIR 2009 | Rank-only formula with `k = 60` in `src/vidxp/search_fusion.py` | Combines modality rankings without treating their raw distances as one scale | Rank-anchored candidate construction, direct temporal matching, one hit per supporting modality, and interval union are VidXP controls, not parts of the paper. |
| Zhao et al., [VideoPrism](https://arxiv.org/abs/2402.13217), ICML 2024, and Google's public LvT checkpoint | Global video-text embeddings and official text canonicalization in `src/vidxp/capabilities/action/` | Supplies cross-modal similarity for short action clips | VidXP's fixed windows and long-video ranking are not VideoPrism methods. The paper's action results use task-specific evaluation heads and do not validate raw similarity as temporal action localization. |
| Tschannen et al., [SigLIP 2](https://arxiv.org/abs/2502.14786), 2025 | Released image-text encoder in `src/vidxp/capabilities/scene/` | Supplies visual-semantic frame retrieval | VidXP samples at 1 fps. These records are sampled frames, not detected semantic scenes. |
| Radford et al., [Whisper](https://arxiv.org/abs/2212.04356), ICML 2023, and Zhang et al., [Qwen3 Embedding](https://arxiv.org/abs/2506.05176), 2025 | Speech recognition and text embeddings in `src/vidxp/capabilities/speech/` | Produces timestamped, searchable transcript evidence | `faster-whisper` is the runtime implementation. Segmentation, storage, and retrieval are VidXP choices. |

Index schema 8 changes the vector metric and sound representation. Rebuild
repositories created by older versions before using this product path.

## Sound replacement decision

The sound provider's task is a free-form acoustic description to timestamped
occurrences. Audio moment retrieval measures sentence-to-interval retrieval on
long recordings; open-vocabulary sound-event grounding measures the short,
repeated, and overlapping sounds that also matter to VidXP. Neither task alone
covers the whole multimodal product query.

| Candidate | Grounded result | Product decision |
| --- | --- | --- |
| PE-AV and PE-A-Frame, Vyas et al., [“Pushing the Frontier of Audiovisual Perception with Large-Scale Multimodal Correspondence Learning”](https://arxiv.org/abs/2512.19687) | Apache-2.0 family. PE-AV jointly embeds audio, video, audio-video, and text; PE-A-Frame produces dense sound-localization scores. | PE-A-Frame Small is selected for sound localization from the frozen AEGBench comparison. PE-AV is not selected for action: the small recognition gate was already at its ceiling, PE-AV has no interval head, and its checkpoint was larger and slower in the smoke. |
| FlexSED, Hai et al. | MIT, 430.9 MB detector checkpoint plus pinned LAION CLAP; produces 25-fps scores for requested event phrases | Not selected. Runtime passed, but it missed the unique siren and drumbeat targets. The reported `0/4` target score is not a provider-quality rate because phone is invalid and engine has multiple correct occurrences. |
| DASM, Cai et al. | The official model hub exposes 636 MB of MIT-marked weights, but released text-query inference hard-codes CUDA and depends on a separate MGA-CLAP checkout and checkpoint | Blocked, not benchmarked. The Transformer4SED source repository has no software license, so VidXP must not copy or port its implementation without clarification. |
| WSTAG, Xu et al. | MIT source and an Apache-2.0 model-hub release provide a CPU code path and 40 ms probabilities; the authors recommend the newer 131.96M-parameter AudioCaps-v2/LAION-CLAP model | Not selected. It missed the unique siren and drumbeat targets at the released threshold. Its engine top result at 242.22 s matches another LongVALE engine-rev annotation, so the current target-only score is not a valid final quality estimate. |
| FLAM/OpenFLAM, Wu et al. | ICML 2025 frame-wise open-vocabulary detector and retrieval model; the public release supports CPU in its example | Blocked. Code and model are non-commercial, and the public OpenFLAM checkpoint is not the internal model behind the paper's reported results. |
| SpotSound, Sun et al. | ACM MM 2026 short-event temporal grounder; directly targets false timestamps and needle-in-a-haystack audio | Research ceiling only. Its 80.8 MB adapter requires the 8B non-commercial Audio Flamingo 3 base and a Linux/CUDA-oriented runtime. |
| TimeAudio, Wang et al. | Long-audio temporal model with explicit time encoding and token merging | Rejected for this deployment before execution: the release requires Vicuna-7B and documents more than 40 GB GPU memory. |
| Official DCASE 2026 MS-CLAP/QD-DETR baseline | Direct interval prediction from one-second features; 13.56 R1@0.7 on the hidden evaluation | Reproducibility control, not the product candidate. Its dependencies conflict with the managed runtime. |
| M2D-CLAP + modified CG-DETR, Kibata et al. | 211.87M parameters and 48.59 R1@0.7, tied first in DCASE 2026 | Quality target only; public code and weights were not verified. |
| CASTELLA-trained UVCOM in Lighthouse | Released checkpoint; 20.3 R1@0.7; at most 300 one-second audio features | Reproducibility control only. A second runtime adds install, storage, and support cost without demonstrated product gain. |

The reference-audio audit found the phone-ring interval at `-91.75 dBFS` RMS
and `-78.27 dBFS` peak despite an explicit ringing annotation. Its MP4 matches
the downloaded archive, so quarantine it from sound-only scoring pending human
review rather than changing its label silently. The engine sound phrase also
has several correct occurrences, including WSTAG's top result inside a separate
LongVALE engine-rev annotation. The old component score therefore has only two
unambiguous cases; FineLAP, FlexSED, and WSTAG miss both. It is superseded for
provider selection by the 149-query AEGBench result. That result justifies
implementing PE-A-Frame Small and rebuilding the sound index; it does not
validate the long-audio serving path.

VidXP splits overlap ownership at the midpoint, maps retained frames to global
timestamps, and stores each timestamp once. Search ranks frames with the model's
dot product, then returns the best frame from each fixed evidence window.

| Sound default | Basis |
| --- | --- |
| 10-second inference section | The product-path smoke took 22.156 seconds with ten-second sections versus 198.651 seconds with 60-second sections on the same 75.81-second video. This is a Mac resource choice, not a PE-A accuracy setting. |
| 2-second inference overlap | Smallest nonzero overlap tested; it preserved context across section edges without increasing the measured run time. Five seconds increased the run to 33.564 seconds and did not change the two checked top results. |
| 10-second evidence window | Returns a playable context chunk while retaining the best 40 ms timestamp as metadata. It does not claim an exact event boundary. |
| Batch size 1 | Conservative default after PE-A reached 5.30 GiB peak RSS in the AEGBench provider run. |

These are configurable VidXP engineering defaults, not PE-A-Frame methods or
measured accuracy optima; the long-audio gate remains required. Distinct
repeated events remain separate windows.

## Original product controls

| Behavior | Exact status |
| --- | --- |
| Fixed VideoPrism records | Sixteen frames sampled at 2 fps form a record of about eight seconds. No paper was adopted to select this temporal unit. |
| Raw VideoPrism similarity ranking | Global LvT cosine similarity ranks the fixed records. This is a product control, not the action-localization method evaluated in the paper. |
| One-second SigLIP 2 records | They provide dense visual evidence, not shot or scene boundaries. |
| PE-A frame-to-evidence retrieval | Chroma uses inner product because PE-A ranks frames by dot product. For each requested evidence result, search reads at most the mathematically bounded number of frames that one ten-second window can contain, then keeps the best frame per distinct window. No empirical over-fetch multiplier or score threshold is used. |
| Rank-anchored direct overlap | A hit seeds a candidate and takes at most the best directly overlapping hit from each other modality. Same-modality hits and indirect overlap remain separate. This is VidXP logic. |
| Candidate interval union | A candidate starts at its earliest supporting hit and ends at its latest. A broad source hit can still produce a broad result, but neighboring hits cannot extend it transitively. |
| Separate candidate and output depth | `top_k` limits final fused results. `candidate_top_k` limits each modality to 100 hits by default. The corrected ten-task replay was identical from 100 through exhaustive input; this supports a resource cap, not a general accuracy optimum. |
| Optional query model | A language model may plan searches or summarize citable evidence. Model size and reasoning are deployment choices, not research adoptions. |

The reverted `4x` over-fetch and anchor-preserving union rule is not adopted. Its
multiplier was selected after one development example and has no general claim.

## Benchmark-only experiments

| ID | Source and scope | Recorded result | Decision |
| --- | --- | --- | --- |
| `p2s_asg_vidxp_v1` | Point-to-Span v1, Section 3.1 only; VidXP score curves and early NMS replace the unreproduced full pipeline | Development IoU changed from `0.7493` to `0.7976`; only sound produced a span, below the direct-inspection agent's `0.8824` | Concluded diagnostic; not adopted |
| `videoprism_overlap_control_v1` | CTAP/Barrios et al. motivate overlapping windows; VidXP replaced the normal action index with four-second windows at a two-second stride | On five held-out action tasks, full-list candidate recall at tIoU 0.5 rose from `0.20` to `0.60` and top-1 recall from `0.00` to `0.20`; a top-three coarse gate reduced candidate recall to `0.40` | Overlapping records remain useful candidates. The previous union and the tested coarse gate are rejected; no product selector is adopted |
| `diwan_shotdetect_siglip2_v1` | Diwan et al. ShotDetect proposals, scored with existing SigLIP 2 records; VidXP added proposal-level RRF | Development IoU reached `0.8902`; on six scene-comparable held-out tasks RRF reduced mean IoU from `0.2841` to `0.1175` | Proposal-level RRF rejected; code retained as a control |
| `manual_modality_query_ceiling_v1` | Luo et al. and TFVTG motivate decomposition, but manual modality wording is a VidXP ceiling rather than either published method | Top-three target coverage changed from 7/16 to 8/16; nine ranks improved and two worsened | Mandatory rewriting rejected |
| `finelap_separate_streams_v1` | FineLAP Sections 3.2–3.3; global windows and dense activations queried separately | Top-three target coverage changed from 0/4 mixed to 3/4 across separate lists | Supports the product rule not to cross-rank the raw outputs; no local-activation product surface selected |
| `finelap-two-stage-held-out` | FineLAP's two representations with VidXP's global top-three gate and pooled local ranking | Gate coverage `2/4`; final top-three coverage `0/4`; mean final IoU `0` against one accepted interval per task | Exact component diagnostic retained, but invalid labels prevent a provider decision; it does not gate the paired multimodal run |
| `candidate-depth-fusion-control-v1` | Original VidXP diagnostic using saved full-query rankings and production connected-component RRF; RRF supplies only the rank formula | Depth 20 improved R@3 and R@10 at tIoU 0.5 from `0.30` to `0.40` versus depth 3, but R@1 stayed `0.20`. At depth 100 R@1 became `0`; full depth produced video-length top intervals. | No candidate depth adopted. Separate event proposals from ranking; do not replace one shared magic depth with another. |
| `candidate-depth-direct-overlap-control-v2` | The same ten saved full-query rankings after replacing transitive components with rank-anchored direct overlap | Depths 100 through all were stable instead of collapsing. At full depth, R@1/R@3/R@5/R@10 at tIoU 0.5 were `.10/.10/.20/.20`. | Direct overlap adopted to preserve separate moments. Candidate collection now has an independent default cap of 100; this is not claimed as a general optimum. |
| `pe-a-frame-small-mac-diagnostic` | Vyas et al. PE-A-Frame Small, exact released checkpoint; one full-track run plus four target-aware recognition clips | Full track: 244.35 s, 4.30 GiB peak RSS, target miss. Target-aware mean best-span IoU: 0.1654 full query, 0.1151 sound-only. | Inconclusive for selection because two sound labels were invalid. Retained as a runtime and failure diagnostic; the AEGBench result supersedes it. |
| `aegbench-sound-seed42-n50` | Vyas et al. PE-A-Frame Small versus Li et al. FineLAP on 50 frozen AEGBench recordings; 149 annotated category queries; every repeated interval; provider default thresholds | PE-A versus FineLAP: frame AUROC `.8614/.8401`; frame AP `.7616/.7484`; top-point accuracy `.7651/.7315`; mean IoU `.5226/.2924`; CPU inference `183.30/17.98` s for 613.43 s of audio. | PE-A-Frame Small selected for sound localization. This is a candidate-selection subset, not a full AEGBench score; long-audio stitching remains unvalidated. |
| `kinetics-mini-videoprism-2026-09-05` | VideoPrism through VidXP's 2 fps/16-frame records on the pinned 50-video, five-class Kinetics-mini validation set | Top-1 `50/50`; 390.49 s inference, or 7.81 s/video. A PE-AV Small 16-frame direct-forward smoke classified one archery clip correctly in 13.36 s; its checkpoint is 3,388,082,648 bytes. | Keep VideoPrism. This small gate establishes basic action recognition only; it does not repair or measure long-video temporal ranking. |
| `flexsed-mac-held-out` | Hai et al. FlexSED, exact detector and LAION CLAP revisions; released non-overlapping ten-second path | 616.7 s audio in 10.85 s; 1.57 GiB peak RSS. Designated target beat surrounding audio on 0/4 full and 0/4 sound-only queries; best target overlap was about 0.045 IoU. | Runtime passes; not selected because it missed both unique valid cases. Overall quality is unscored until repeated sound occurrences are labeled. |
| `dasm-release-compatibility-2026-09-05` | Cai et al. DASM; official Transformer4SED revision `c3e883d0fbeaf7031b467d45a3c46a88a76c00b6` and official model-hub tree | The hub contains 636 MB of detector/query artifacts. The only released interactive inference is a CUDA notebook with a hard-coded local path and external MGA-CLAP code/weights; the code repository has no license. | Blocked before model execution. This is an artifact, runtime, and licensing failure—not a quality result. |
| `wstag-audiocaps-v2-mac-held-out` | Xu et al. architecture through the authors' newer recommended model `c1ede4afca77acb67bbd20e48e3fc4657b96666a`; LAION CLAP `365dea6ef167def6676140ed93bbc43f84dabb28` | Three audible full tracks: 0/3 designated-target wins in both wording modes; official threshold produced zero designated-target overlaps. Six CPU forwards took 25.82 s and peaked at 4.15 GiB RSS. | Not selected: both unique valid cases were missed. The engine top at 242.22 s is another annotated rev, so no overall provider score is claimed. This is a post-paper checkpoint, not the model reported in 2024. |

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
- FineLAP's global and local records could not be treated as one raw-distance
  ranking, and its unvalidated global gate is no longer product behavior.
  PE-A-Frame Small now supplies one frame-level score space. FineLAP remains only
  in benchmark code for reproducibility.
- RRF is useful as a transparent ranking control, but the current temporal
  grouping and union do not provide exact boundaries.
- The original fusion chained adjacent records into video-length moments as
  candidate depth increased. Rank-anchored direct overlap removes that failure;
  the full-depth replay is now stable. PE-A-Frame, VideoPrism, and SigLIP 2 define
  representations, not VidXP's grouping. LongVALE Section 3.2 constructs
  single-modal semantic events before combining modalities; that supports the
  proposal-first direction but is not a drop-in algorithm for raw records.
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
