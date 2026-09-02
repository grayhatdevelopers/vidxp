# Multimodal retrieval and temporal-localization direction

Collection index: [Benchmarking research](README.md)

Status: Current decision record; component providers are implemented, while the
temporal architecture remains under evaluation

Last verified: 2026-09-02

Research provenance: [Research adoption record](research_adoption.md). That
record is authoritative for what is implemented; papers listed here are not
adopted unless it says so there.

## Required product behavior

VidXP must find inspectable evidence for visual events, environmental sounds,
and speech, then return useful time ranges. Results must preserve modality and
source provenance. That requirement does not prescribe separate indexes, one
shared model, late fusion, or query-time processing; those are alternatives to
measure.

LongVALE is the closest combined benchmark because its event descriptions can
depend on vision, generic audio, speech, or their temporal relationship. It does
not determine the internal architecture.

## Current implementation and known limitation

The current control uses separately indexed evidence:

- VideoPrism action records contain 16 frames sampled at 2 frames per second,
  producing non-overlapping intervals of about eight seconds;
- scene records contain frames sampled at 1 frame per second;
- FineLAP supplies global sound windows and dense timestamped activations; and
- faster-whisper plus Qwen3 text embeddings supply timestamped speech evidence.

Fusion groups every overlapping hit into a connected component, scores the
component with reciprocal rank fusion, and returns the union from the earliest
start to the latest end. A relevant coarse action hit can therefore expand a
more precise sound or speech interval. Ranking and boundary accuracy are
separate properties: a correct top candidate can still have avoidably poor IoU.

The completed post-FineLAP-fix trace demonstrates this failure directly. For a
0–6 second event, action rank 1 covered 0–8.0075 seconds, scene ranks 1–3 covered
1.001–4.004 seconds, and sound ranks 1–3 covered 1.76–2.24 seconds. Fusion ranked
that opening component first but returned 0–8.0075 seconds because interval
union preserved the full action record. This trace does not show an
encoder-ranking failure; it does not establish ranking quality beyond this
development query.

A subsequent all-record probe confirms both limits on the same query. The
three encoders rank the opening region correctly, but `top_k = 3` excludes the
later dense scene and sound records needed to see its end. In the complete
timelines, scene relevance falls after about 7.007 seconds and FineLAP
activation relevance drops sharply between seconds 6 and 7. Current fusion
cannot use that transition and still returns the full 0–8.0075-second action
record. The dense evidence therefore supports a boundary near seven seconds;
it does not justify changing the result to the annotated six seconds by hand.

## Separate the architectural questions

| Layer | Question | Relevant research | What the evidence supports |
| --- | --- | --- | --- |
| Temporal representation | Should candidates be fixed clips, dense frames, shots, scenes, or learned proposals? | CTAP, Barrios et al., LGSS, ShotCoL, BaSSL, NeighborNet, Diwan et al., and STITCH | Overlapping windows and content-aligned proposals are established alternatives to arbitrary non-overlapping windows. Fixed windows still need boundary refinement and can multiply candidates; scene boundaries alone do not locate brief events inside a scene. |
| Candidate selection | Which evidence should a query send to a downstream model? | BOLT, Point-to-Span, and adaptive-keyframe work | Query-conditioned sampling helps under a frame budget. VidXP now has a benchmark-only adaptation of Point-to-Span's span generator; it is not a full reproduction or product path. |
| Interval prediction | How should start and end times be inferred? | Moment-DETR, UMT, QD-DETR, UniVTG, REZE, and Anchor-Aware Similarity Cohesion | Trained models directly predict intervals or boundary scores; REZE instead aggregates frozen-VLM confidence curves. These have different training, compute, and artifact assumptions and must be compared as separate controls. |
| Multimodal combination | Should modalities remain separate, interact before prediction, or use one model? | UMT, QD-DETR, AVicuna, LongVALE, and modality-specific systems | Late fusion is a transparent control, not a settled product direction. Learned audiovisual interaction is established, but available implementations vary in training assumptions and local-runtime fit. |
| Answer synthesis | Should a language model inspect selected evidence? | BOLT and long-video VLM work | A language model may explain or verify timestamp-bound evidence. It must not invent boundaries that the retrieval/localization path cannot support. |

These layers can be combined. Selecting a frame sampler does not select a
boundary model, and selecting a scene detector does not select a fusion rule.

## Maturity and applicability

| Work | Maturity and artifacts | Direct use for VidXP | Important limit |
| --- | --- | --- | --- |
| [LGSS](https://openaccess.thecvf.com/content_CVPR_2020/html/Rao_A_Local-to-Global_Approach_to_Multi-Modal_Movie_Scene_Segmentation_CVPR_2020_paper.html), [ShotCoL](https://openaccess.thecvf.com/content/CVPR2021/html/Chen_Shot_Contrastive_Self-Supervised_Learning_for_Scene_Boundary_Detection_CVPR_2021_paper.html), [BaSSL](https://github.com/kakaobrain/bassl), and [NeighborNet](https://openaccess.thecvf.com/content/CVPR2024/html/Tan_Neighbor_Relations_Matter_in_Video_Scene_Detection_CVPR_2024_paper.html) | Peer-reviewed 2020–2024 lineage; multiple code releases and public scene benchmarks | Compare fixed action clips with shot- or scene-aligned candidates | Movie-scene segmentation is not arbitrary natural-language moment grounding. |
| [Moment-DETR](https://github.com/jayleicn/moment_detr), [UMT](https://github.com/TencentARC/UMT), [QD-DETR](https://github.com/wjun0830/QD-DETR), and [UniVTG](https://github.com/showlab/UniVTG) | Peer-reviewed 2021–2023 work with official code and checkpoints | Established interval-prediction controls; UMT/QD-DETR test audiovisual input | Most checkpoints are target-trained and use older CUDA-oriented environments. Published scores are not zero-shot VidXP expectations. |
| [BOLT](https://github.com/sming256/BOLT) | CVPR 2025 with official MIT-licensed code; recent and lightly maintained | Compare query-aware frame selection with uniform sampling | Evaluated on video question answering, not temporal IoU; no start/end output. |
| [Automatic Funny Scene Extraction](https://ojs.aaai.org/index.php/AAAI/article/view/41480) | IAAI 2026 applied system; scene-localization modules reported operational at Prime Video; no public end-to-end code or checkpoint found | Evidence for shot detection, multimodal scene construction, then task-specific ranking | Its 98% localization figure is curator judgment of proper scene endings on five movies, not query-conditioned IoU. Humor classification does not generalize automatically to open queries. |
| [TimeLens2](https://github.com/MCG-NJU/TimeLens2) | arXiv 2026 with released 2B/4B/8B checkpoints; too recent for independent maturity | Recent visual temporal-grounding ceiling | Visual-only and materially heavier than established interval baselines; not selected as the default. |
| [AVicuna](https://ojs.aaai.org/index.php/AAAI/article/view/32784) | AAAI 2025 audiovisual temporal model trained on 114,081 pseudo-untrimmed examples | Evidence that a unified model can align audiovisual events and intervals | A trained 7B-class stack is not a drop-in commodity-hardware replacement. |

The funny-scene result belongs to a broader multimodal-humor lineage. FunnyNet
(ACCV 2022) and FunnyNet-W (IJCV 2024) found that audio provides important cues
for funny-moment detection. Those findings support retaining acoustic evidence;
they do not establish a general retrieval architecture.

## Current component status

| Capability | Current control | Candidate evidence | Decision status |
| --- | --- | --- | --- |
| Speech | faster-whisper plus Qwen3 text embeddings | Released ASR and transcript-retrieval benchmarks | Retain as the control; speech and environmental sound remain distinct evidence types. |
| Environmental sound | FineLAP global and dense features | LAION-CLAP as a mature retrieval control; PE-A-Frame and AEGBench for boundaries | Implementation exists, but quality and boundary claims remain pending. |
| Visual retrieval | VideoPrism action clips and SigLIP2 scene frames | MVEB places Qwen3-VL-Embedding highly, but does not compare VideoPrism | Qwen is a candidate, not a selected replacement. Run the same retrieval protocol before changing providers. |
| Temporal units | Fixed action clips plus one-second scene records | Shot/scene segmentation and denser query-aware proposals | Open. Existing indexes do not have to be retained if another representation wins on quality and resource use. |
| Boundary inference | Connected-component interval union | Point-to-Span adaptive expansion; Diwan et al. and TFVTG controls | The fixed-window widening failure is confirmed. The first P2S adaptation improved one sound-led case but generated no scene or action span. |
| Fusion | RRF scoring inside connected interval components | Learned audio-visual interaction or query-conditioned boundary scoring | Retain as the transparent control only. RRF is paper-derived; connected grouping and interval union are VidXP-specific. Provenance must survive any replacement. |
| Planner and synthesis | Structured evidence passed to the configured agent/model | Smaller local planners or selected media verification | Evaluate separately from retrieval. Agent prose cannot substitute for temporal evidence. |

## Decision measurements

Evaluate alternatives on identical media, queries, ground truth, and output
rules. Report:

- mean IoU and R@1 at tIoU 0.3, 0.5, and 0.7;
- absolute start error, end error, and duration error;
- candidate recall before boundary refinement and final top-k relevance;
- indexing or preprocessing time, stored bytes, query latency, and peak memory;
- results by modality and for genuinely joint queries; and
- artifact license, pinned revision, operating-system support, and failure mode.

Published tables guide candidate selection only when the task, inputs, output
unit, training regime, and split match. A high whole-video retrieval score does
not prove timestamp quality. A high VQA score does not prove retrieval. A
target-trained temporal score is a ceiling, not a direct zero-shot comparison.

## Bounded decision sequence

1. Treat the current RRF result as coarse retrieval. The completed trace already
   establishes correct top-region ranking for the development case; do not rerun
   the obsolete pre-tokenization failure.
2. Retain `p2s_asg_vidxp_v1` as a concluded diagnostic. On the development
   query it generated only a sound span and remained below the direct-
   inspection baseline, so do not spend a full agent batch on this adaptation
   alone.
3. Compare the current eight-second non-overlapping action representation with
   shorter overlapping action records and a content-aligned proposal control.
   Freeze exact durations and strides before held-out scoring and label them as
   VidXP experiment settings, not paper parameters.
4. Apply the same candidate and interval policy to each representation. Report
   candidate recall, IoU and boundary errors, indexing time, stored bytes,
   query latency, peak memory, and record count.
5. Run metered agent comparisons only after the representation paths pass local
   validation and the maintainer confirms the run.

The current Codex MCP smoke is diagnostic development data. It shows that the
agent used the skill and MCP successfully and returned relevant evidence, but
one paired task cannot select an architecture or support a LongVALE claim.

## Benchmark roles and execution policy

| Benchmark | Decision use | Does not establish |
| --- | --- | --- |
| MAEB and MVEB | Broad component-embedding context | Long-video interval quality or VidXP system behavior |
| OVSD and MovieNet scene segmentation | Temporal-unit and scene-boundary regression | Natural-language moment retrieval or multimodal fusion |
| QVHighlights, Charades-STA, and related grounding sets | Query-conditioned interval and highlight evaluation | Generic zero-shot transfer unless the exact training regime says so |
| AEGBench | Environmental-sound interval quality | Visual or speech retrieval |
| LongVALE | Combined vision, sound, and speech temporal grounding | Actor clustering or unmeasured production performance |
| Codex MCP ablation | End-to-end agent workflow, tool use, latency, and usage | Component-model leaderboard or full LongVALE result |

Reading papers and inspecting open artifacts does not consume model inference.
Running local checkpoints consumes storage, memory, electricity, and time.
Metered agent runs require explicit approval. Full benchmark runs follow only
after the bounded diagnostic identifies a decision that the run can resolve.
