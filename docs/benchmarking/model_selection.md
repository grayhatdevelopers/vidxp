# Multimodal retrieval and temporal-localization direction

Collection index: [Benchmarking research](README.md)

Status: Current decision record; component providers are implemented, while the
temporal architecture remains under evaluation

Last verified: 2026-09-02

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

Search now fetches up to four times the requested result count per modality,
capped at 100, before returning the requested number of moments. Fusion groups
adjacent hits from one modality, keeps overlapping hits from other modalities
as supporting evidence, and uses the strongest continuous group as the returned
boundary. This prevents one coarse action hit from automatically stretching a
more precise scene or sound range.

This is still a retrieval-based boundary estimate. An action-only result keeps
the action record's roughly eight-second range, and no benchmark score is
claimed for the new fusion profile yet.

## Separate the architectural questions

| Layer | Question | Relevant research | What the evidence supports |
| --- | --- | --- | --- |
| Temporal representation | Should candidates be fixed clips, dense frames, shots, scenes, or learned proposals? | LGSS, ShotCoL, BaSSL, NeighborNet, and the Prime Video funny-scene system | Shot-aware semantic units are an established alternative to arbitrary fixed windows, especially for edited long-form video. Scene boundaries alone do not locate brief events inside a scene. |
| Candidate selection | Which evidence should a query send to a downstream model? | BOLT and adaptive-keyframe work | Query-conditioned sampling improves long-video VQA under a frame budget. BOLT selects frames; it does not predict an event interval. Its pre-extracted frame features are still an offline feature store. |
| Interval prediction | How should start and end times be inferred? | Moment-DETR, UMT, QD-DETR, and UniVTG | Query-conditioned models directly predict moments or boundary scores. UMT and QD-DETR include audio on QVHighlights; this is not a visual-only research problem. |
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
| Boundary inference | Strongest continuous same-modality run, with overlapping evidence retained | Shot-aware proposals and query-conditioned interval models | Improved control; still open for action-only and learned boundaries. |
| Fusion | Anchored reciprocal rank fusion | Learned audio-visual interaction or query-conditioned boundary scoring | Keep as the transparent control. Provenance must survive any replacement. |
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

1. Measure the best interval IoU representable by the current raw hits. This
   distinguishes a representation ceiling from a ranking or fusion defect.
2. Compare the current fixed units with shot-aligned, scene-aligned, and denser
   candidates on the same development examples. Do not change the production
   index format for this probe.
3. If suitable candidates exist but their boundaries remain poor, compare an
   established query-conditioned interval method before a recent multi-billion-
   parameter model.
4. Compare late fusion with audiovisual interaction only after the candidate
   and boundary stages are measured separately.
5. Promote a new architecture only after a bounded local runtime check and a
   benchmark whose protocol matches the claimed behavior.

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
