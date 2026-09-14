# Multimodal model and benchmark direction

Collection index: [Benchmarking research](README.md)

Status: Current decision record; FineLAP and VideoPrism are implemented, while
other candidate providers remain planned unless architecture says otherwise

Last verified: 2026-08-30

## Product requirement

VidXP needs three independently searchable, timestamped evidence channels:

1. visual scenes and actions;
2. environmental sounds, music, and other non-speech acoustic events; and
3. spoken words through ASR and text retrieval.

Query-time fusion must preserve which channel produced each hit. A shared
embedding model is optional; collapsing the channels is not the requirement.
LongVALE makes this boundary explicit because its events can depend on vision,
generic audio, speech, or their temporal relationship.

## Repository baseline

The history before this change contained no shipped CLAP provider or generic-sound
capability. CLAP appears in the later landscape/roadmap research, not in the
application implementation history, so it was not removed by the VideoPrism
change. This branch now implements the missing layer with FineLAP; LAION-CLAP
remains the mature comparison rather than the production provider.

VideoPrism is different: it is a current, separately registered temporal-video
capability using `google/videoprism-lvt-base-f16r288` through Transformers. The
new model direction keeps that implementation as the incumbent control while
testing whether Qwen3-VL-Embedding improves text-to-video scene/action retrieval.

## How models are selected

Published benchmark tables, release history, licensing, adoption, artifact
format, and runtime size are sufficient to choose the first integration
candidates. VidXP does not need to spend model or agent runs recreating public
leaderboards before implementation.

Local evaluation has a narrower purpose: verify preprocessing, timestamps,
memory, latency, index size, failure behavior, and regressions in this repository.
It does not substitute a tiny private sample for broad published comparisons.
Promptfoo is therefore not required for component-model selection. It is the
selected runner for the separate [Codex MCP-on/MCP-off agent
ablation](agent_ablation.md), where paired task execution, repetitions, traces,
and usage accounting are part of the question. VidXP's Python benchmark code
continues to own dataset preparation and deterministic temporal scoring.

## Current provider direction

| Role | First direction | Control or ceiling | Reason |
| --- | --- | --- | --- |
| Speech transcription and semantic search | Keep faster-whisper plus the current Qwen3 text-embedding path | Existing released-ASR benchmark paths | Speech and acoustic-event retrieval are different tasks; MAEB shows that no single audio encoder dominates linguistic and environmental-sound work. |
| Environmental-sound retrieval | [FineLAP](https://github.com/xiquan-li/FineLAP), now integrated | [LAION-CLAP](https://github.com/LAION-AI/CLAP) as the mature native-Transformers baseline | FineLAP combines global audio-text retrieval with dense frame features and leads the checked same-table AudioCaps comparison. VidXP supplies fixed ten-second windowing and timestamped dense records. |
| Open-vocabulary sound localization | FineLAP dense features, now stored; compare [PE-A-Frame](https://github.com/facebookresearch/perception_models) | AEGBench methods as research ceilings | Clip retrieval alone cannot identify exact sound intervals, especially repeated or overlapping events. FineLAP integration does not establish boundary quality until AEGBench or LongVALE is run. |
| Visual scene/action retrieval | Evaluate [Qwen3-VL-Embedding-2B](https://huggingface.co/Qwen/Qwen3-VL-Embedding-2B) as the practical candidate | Qwen3-VL-Embedding-8B as the quality ceiling; VideoPrism as the incumbent control | MVEB's text-video table ranks Qwen 8B and 2B first and second. The checked table has no directly comparable VideoPrism row, so this is stronger current selection evidence, not proof that VideoPrism lost a head-to-head. |
| Visual temporal grounding | Evaluate [TimeLens2-4B](https://github.com/MCG-NJU/TimeLens2) after candidate retrieval | TimeLens2-8B and existing temporal baselines | The published 4B average nearly matches 8B at much lower cost. TimeLens2 is visual-only and cannot replace the sound or speech channels. |
| Cross-modal fusion | Keep modality-specific providers and fuse timestamped candidates | A unified permissive audio-video-text encoder can be a later comparison | Separate providers preserve provenance, allow independent upgrades, and match the evidence that different model families lead different modalities and tasks. |
| Query planning and answer synthesis | [Qwen3.5 4B](https://huggingface.co/Qwen/Qwen3.5-4B) through official Ollama `qwen3.5:4b-q4_K_M` | Qwen3.5 9B as a higher-memory comparison | The 4B model has strong published instruction-following and agent results while its official Q4_K_M artifact is approximately 3.4 GB, about half the 9B artifact. VidXP needs bounded schema generation over retrieved evidence, not a second retrieval encoder. |
| Future media evidence enrichment | Reuse Qwen3.5 vision for selected keyframes before adding another model | Evaluate an audio-video model only for top uncitable sound/action hits | The current adapter sends JSON evidence, so multimodal model support alone changes nothing. Media inputs must remain timestamp-bound derived evidence and must not replace FineLAP, scene, action, or speech retrieval. |

Before promotion, every new checkpoint still needs an immutable revision, artifact
hash, license review, safe-loading review, dependency fit, and a bounded real-media
smoke test.

## Published selection evidence

Scores are comparable only within the named paper and task.

| Source and task | Relevant result | Decision use |
| --- | --- | --- |
| [FineLAP, AudioCaps retrieval](https://aclanthology.org/2026.acl-long.473/) | FineLAP T→A/A→T R@1: 45.7/62.5; the paper's LAION-CLAP row: 35.1/44.2 | Select FineLAP for the first sound integration and retain CLAP as the mature control. |
| [MVEB text-video leaderboard](https://arxiv.org/abs/2606.14958) | Qwen3-VL-Embedding-8B: 60.9 mean; 2B: 58.1; LCO-Embedding-Omni-7B: 56.8 | Prefer Qwen 2B for the practical visual candidate and 8B only when maximizing published quality. |
| [TimeLens2 visual grounding](https://github.com/MCG-NJU/TimeLens2) | Seven-dataset average mIoU: 47.7 for 4B and 48.0 for 8B | Start with 4B; the 0.3-point gain does not justify making 8B the default candidate. |
| [AEGBench](https://arxiv.org/abs/2607.04383) | PE-A-Frame Large: 0.389 mIoU, 0.407 event-F1, 0.607 segment-F1 in the checked table | Use a released specialist to test exact open-vocabulary sound intervals. |
| [Qwen3.5 4B model card](https://huggingface.co/Qwen/Qwen3.5-4B) | Vendor-reported MMLU-Pro 79.1, IFEval 89.8, BFCL-V4 50.3, and TAU2-Bench 79.9; native 262,144-token context | Select the first local planner/synthesizer from published quality evidence; validate only schema retention, grounding, resource use, and failure behavior in VidXP. |
| [Official Ollama Q4_K_M artifact](https://ollama.com/library/qwen3.5:4b-q4_K_M) | 4.66B parameters, Q4_K_M, approximately 3.4 GB, Apache-2.0 | Use the official cross-platform build and an explicit pull instead of bundling weights or relying on a community conversion. |

VideoPrism remains a credible multi-frame video encoder. The decision above does
not reject it on quality. It rejects two unsupported claims: that implementation
friction still blocks it, and that it is automatically the first text-video
retrieval pick despite being absent from the current common MVEB comparison.

## What each dataset or benchmark contributes

| Dataset or benchmark | Use in VidXP | Does not establish |
| --- | --- | --- |
| [MAEB](https://arxiv.org/abs/2602.16008) | Broad audio-embedding selection across speech, music, environmental sound, and audio-text tasks | Long-video timestamp accuracy or end-to-end VidXP quality |
| [MVEB](https://arxiv.org/abs/2606.14958) | Common video-embedding selection across retrieval and other representation tasks, including paired video-only and audio-plus-video variants | A direct VideoPrism comparison, unrestricted temporal localization, or system latency |
| [AEGBench](https://arxiv.org/abs/2607.04383) | Open-vocabulary environmental-sound interval grounding, including difficult and repeated events | Visual or speech retrieval |
| [LongVALE](https://github.com/ttgeng233/LongVALE) | Primary combined target: Omni-TVG for vision, sound, and speech event localization in long videos | Actor clustering; its captioning tasks are relevant only if VidXP claims generation |
| [FLARE](https://flarebench.github.io/) | Secondary long-video retrieval stress test with visual-only, audio-only, and hard joint queries | Human-authored-query generalization; the queries are model-generated and filtered |
| [OVSD](https://research.ibm.com/publications/robust-and-efficient-video-scene-detection-using-optimal-sequential-grouping) | Open-licensed scene-boundary segmentation data and a useful temporal-unit regression set | Natural-language retrieval, action recognition, environmental-sound search, speech search, or cross-modal fusion |
| [MultiVENT 2.0](https://huggingface.co/datasets/hltcoe/MultiVENT2.0) | Large-corpus event retrieval for visual, ASR, OCR, and metadata channels | Generic acoustic-event retrieval or moment boundaries |

LongVALE supplies three tasks: omni-modal temporal grounding, dense video
captioning, and segment captioning. Omni-TVG directly matches VidXP's search and
timestamp contract. The two captioning tasks should not be adopted merely because
they share the dataset.

## Remaining benchmark gap

A generic centralized audio or video embedding leaderboard is not new white
space: MAEB and MVEB already provide that infrastructure in the MTEB ecosystem,
and AEGBench, LongVALE, and FLARE cover adjacent temporal and multimodal slices.

The defensible gap is narrower: a live, reproducible long-video system benchmark
that combines scene/action, environmental-sound, and speech queries; scores both
retrieval and exact boundaries; includes modality-isolation and fusion ablations;
uses realistic queries; and reports latency, memory, index size, and
commodity-hardware behavior. If VidXP publishes this, it should extend or
interoperate with the MTEB/MOEB ecosystem instead of creating an isolated model
leaderboard.

## Cost and execution policy

Reading published papers, leaderboards, model cards, and open benchmark metadata
does not consume Codex, Claude, or model-inference runs. Downloading and running
open checkpoints locally normally has no per-call API charge, but it does consume
the machine's storage, memory, electricity, and time; dataset and checkpoint
licenses can also restrict use.

Metered model or agent comparisons are not part of the selection gate. Spend
local compute only after the provider exists, using the smallest smoke that can
catch integration defects. Schedule full MAEB, MVEB, LongVALE, FLARE, or AEGBench
runs only when their result answers an approved paper or release question.

## Implementation order

1. Validate the implemented FineLAP sound capability on a bounded real-media
   sample, then run the LongVALE one-archive adapter pilot.
2. Compare LAION-CLAP as the mature integration baseline and PE-A-Frame where
   boundary quality requires a specialist.
3. Add or replace the visual video-embedding provider with
   Qwen3-VL-Embedding-2B while keeping current and VideoPrism controls.
4. Add TimeLens2-4B only after cheap candidate retrieval, for visual temporal
   proposal or reranking work.
5. Run LongVALE Omni-TVG and FLARE with all three evidence channels and frozen
   fusion. Keep OVSD as a scene-boundary component test.
