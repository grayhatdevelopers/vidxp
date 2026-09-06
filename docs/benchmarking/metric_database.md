# VidXP metric database

Last verified: 2026-09-06

This is the public index of VidXP's measured results. Each result identifies the
research protocol or method it tests, VidXP's deviation from that work, the
machine used, and the conclusion the evidence supports. The tables contain the
relevant measurements; they do not depend on one maintainer's local files.

Use [published comparison results](published_results.md) for other systems'
reported scores and [research adoption](research_adoption.md) for the smaller
list of ideas accepted into VidXP. Scores below use proportions from `0` to `1`
unless a percent sign is shown.

## Research question and protocol

The whole-system benchmark asks whether giving the same Codex agent VidXP's
already-indexed video evidence preserves useful retrieval while reducing agent
tokens and, ideally, elapsed time. It does not ask VidXP to trim a two-second
event into a two-second deliverable.

| Item | Fixed protocol |
| --- | --- |
| Evidence unit | Return up to three distinct 8–12-second clips in ranked order. Success@3 requires at least one clip to cover half of the annotated event that can fit in 10 seconds. Returning fewer candidates is valid. |
| Data | Ten selected, LongVALE-derived tasks over five videos, covering scene, action, sound, speech, and joint evidence. The development smoke uses the first task; the held-out pilot uses the remaining nine. This is not an official LongVALE score. |
| Timed starting state | Direct-local and clean-user receive hard links to the same media bytes. VidXP-on receives the index built from those bytes but no source-media path in its workspace; detected host-path bypasses are excluded. All five videos start indexed for scene, action, sound, and speech. Dataset download, model preparation, media import, and indexing are outside agent time. |
| Comparison | Same Codex model, reasoning effort, neutral user prompt, output schema, and fresh state. VidXP-on has the shipped skill and MCP; direct-local has system commands plus host FFmpeg and ffprobe but no VidXP; clean-user starts with OS tools plus terminal and network. |
| Decision | Across every matched, condition-valid pilot pair, VidXP must match or improve direct-local bounded-chunk Success@3 and use fewer total agent tokens. Missing, contaminated, or unscorable primary pairs make the gate unscored. Success@1, rank, latency, Promptfoo cost, calls, IoU, R@K, and boundary errors remain visible. |
| Repetition | The pilot defaults to three repetitions with rotated serial condition order. Per-run values, means, totals, and failures are retained. |
| Machine identity | Every new test row and repository export carries a stable repository ID such as `mac-m2-01`. The table below defines that ID; no hardware serial number or host-generated UUID is stored. |
| Offline cost | Indexing is measured separately on fresh isolated indexes. The agent benchmark must not hide that cost or add it to only the VidXP-on response time. |
| Required isolation | Separate workspaces and homes prevent state reuse. A Codex permission profile denies filesystem-root access and reopens only minimal runtime paths, the current condition workspace, and—for direct-local—the FFmpeg installation prefix. On macOS, preflight tests those OS-enforced boundaries before model calls; the scorer separately rejects detected bypasses. |

The task design comes from
[LongVALE](https://openaccess.thecvf.com/content/CVPR2025/papers/Geng_LongVALE_Vision-Audio-Language-Event_Benchmark_Towards_Time-Aware_Omni-Modal_Perception_of_Long_Videos_CVPR_2025_paper.pdf);
the practical ten-second serving unit and product gate are VidXP evaluation
choices. See [agent ablation](agent_ablation.md) for the executable method and
[research adoption](research_adoption.md) for paper-derived product decisions.

## Machines used

| ID | Hardware | Software and execution | Applies to |
| --- | --- | --- | --- |
| `mac-m2-01` | MacBook Pro `Mac14,7`; Apple M2; 8 CPU cores (4 performance, 4 efficiency); 10 GPU cores; 8 GB memory; ARM64 | macOS 15.6.1 (`24G90`); Python 3.14.7; PyTorch 2.13.0; Transformers 5.14.1; ChromaDB 1.5.9; NumPy 2.5.1; FFmpeg 8.1.1; Node.js 22.23.2; Promptfoo 0.122.2. VidXP selected CPU; PyTorch reported neither MPS nor CUDA available. | September 2026 agent and component rows. Selected exports carry this stable ID; the hardware/software definition remains centralized here. The ID on older exports is a retrospective assignment, not a machine snapshot captured by those runs. |
| `win-hp-01` | HP ENVY Laptop 16-h0xxx; Intel Core i7-12700H; 14 cores, 20 logical processors; 15.72 GiB memory; NVIDIA RTX 3060 Laptop GPU with 4 GiB VRAM | Windows 11; Python 3.14.0; PyTorch 2.13.0+cpu; Transformers 5.14.1; Sentence Transformers 5.6.1; ChromaDB 1.5.9. The GPU was present but unused. | July 2026 official-adapter rows. Current-provider manifests contain this snapshot; surviving legacy artifacts do not contain every package or immutable model revision. |

## System evaluated

| Evidence | Provider and revision | Product representation | Research boundary |
| --- | --- | --- | --- |
| Speech | faster-whisper `large-v3-turbo@0a363e9` and Qwen3 Embedding `0.6B@97b0c61` | Timestamped transcript segments | [Whisper](https://arxiv.org/abs/2212.04356) supplies transcription and [Qwen3 Embedding](https://arxiv.org/abs/2506.05176) supplies semantic retrieval. Benchmarks that provide transcripts do not test transcription. |
| Scene | SigLIP 2 `base-patch16-224@75de2d5` | Frames sampled at 1 fps | [SigLIP 2](https://arxiv.org/abs/2502.14786) supplies image-text similarity. It does not predict scene or event boundaries. |
| Action | VideoPrism `lvt-base-f16r288@fb6de9f` | Sixteen-frame clips sampled at 2 fps, normally about eight seconds | [VideoPrism](https://arxiv.org/abs/2402.13217) supplies global video-text embeddings. VidXP's fixed windows and raw long-video ranking are not the paper's action-localization method. |
| Sound | PE-A-Frame Small `e5fc71c`; FineLAP `b419aa2` benchmark control | PE-A frames are indexed every 40 ms through ten-second inference sections with two-second overlap, then reduced to distinct ten-second evidence windows at search time. | [PE-AV](https://arxiv.org/abs/2512.19687) establishes the model and dot-product frame score. Sectioning, midpoint overlap ownership, and evidence windows are configurable VidXP controls, not paper-derived settings. |
| Fusion | No model | Rank-anchored candidates with at most one directly overlapping hit per supporting modality | [RRF](https://doi.org/10.1145/1571941.1572114) defines `sum(1 / (60 + rank))`. Candidate construction and interval union are VidXP rules; indirect overlap cannot join separate moments. |

Full immutable revisions are pinned in the
[speech](../../src/vidxp/capabilities/speech/specs.py),
[scene](../../src/vidxp/capabilities/scene/specs.py),
[action](../../src/vidxp/capabilities/action/specs.py), and
[sound](../../src/vidxp/capabilities/sound/specs.py) specifications. A row below
states when an experiment replaces these normal representations.

## Agent product metrics

| Metric | Definition | Role and research boundary |
| --- | --- | --- |
| Bounded-chunk Success@3 | At least one of up to three ordered 8–12-second results covers `0.5` of `min(annotation duration, 10 seconds)` | Primary per-task product retrieval metric. The ten-second target and three-result limit are VidXP serving choices, not LongVALE metrics. They reject blink-length, whole-video, and unbounded-list answers. |
| Success@1 and reciprocal rank | Whether the first clip succeeds, and `1 / first successful rank` | Exposes ordering quality without making a top-one miss erase useful evidence returned immediately after it. |
| Paired product gate | VidXP-on Success@3 is at least VidXP-off, and VidXP-on uses fewer total agent tokens | Primary whole-system decision. Candidate count, cost, latency, and calls remain reported separately, so returning more clips does not hide its overhead. |
| Temporal IoU and R@1/R@3 at tIoU 0.3/0.5/0.7 | Exact predicted intervals against the LongVALE-derived annotation | Retained secondary boundary-quality diagnostics. Poor exact trimming and ordering remain product shortcomings and future research targets. |

The two older September development runs used the earlier exact-interval prompt.
The later smoke and first pilot used one bounded clip. The next isolated run
uses the ranked three-candidate contract above. Historical results are not
rescored as if their agents had been allowed to return three clips.

## Input integrity checks

| Check | Machine | Result | Decision |
| --- | --- | --- | --- |
| LongVALE-derived sound references | `mac-m2-01`; PCM levels measured over each exact reference interval and all annotations for the engine video checked | Siren RMS/peak `-18.83/-3.75 dBFS`; engine `-19.22/-2.81`; phone `-91.75/-78.27`; drumbeat `-39.60/-17.18`. The phone video matches the downloaded archive at SHA-256 `0468c1bde02a752d1f20ab370556e59768a5da19519ea1cfe4a0e9760fd5b2f7`. The engine video has several annotated rev/roar intervals; WSTAG's 242.22 s top lies inside the separate 241.760–243.554 s rev annotation. | The custom sound-only score is invalid for phone and engine. Keep the original intervals in the collective LongVALE tasks, where visual and action details disambiguate them; report the sound limitation instead of changing the multimodal labels. |

## Whole-system agent measurements

The agent runs compare the same Codex model with VidXP MCP evidence, direct
local inspection, and a clean-user bootstrap condition. VidXP-on begins with
the five pilot videos already indexed in all four modalities; all agent times
exclude download, preparation, import, and indexing.

### First held-out pilot audit

Evaluation
[`eval-dxR-2026-09-06T00:15:35`](runs/eval-dxR-2026-09-06T00-15-35.json)
completed 81 runs: nine tasks, three conditions, and three repetitions on
`mac-m2-01`. Wall time was 10,524.855 seconds, or 2 h 55 min 24.855 s. The raw
Promptfoo artifact preserves the at-run scores; the table below is the current
deterministic re-audit of its saved responses, traces, and VidXP jobs.
This historical pilot required one final candidate, so its hits are Success@1;
it cannot be rescored as though the agents returned three.

| Condition | Validity and quality | All-run efficiency | Recorded activity |
| --- | --- | --- | --- |
| VidXP | 18/27 condition-valid and scorable; 9/18 bounded hits. Before validity filtering: 16/26 scorable outputs hit. | 92.625 s and 276,456 tokens per run; 7,464,325 tokens total; $13.647699 Promptfoo estimate | 305 model turns; 258 tools: 192 MCP and 66 shell; 27 skill loads |
| Direct local | 25/27 condition-valid and scorable; 12/25 bounded hits. Before validity filtering: 14/27 hit. | 110.877 s and 301,162 tokens per run; 8,131,363 tokens total; $17.334364 estimate | 346 model turns; 327 shell tools |
| Clean user | 22/27 condition-valid and scorable; 15/22 bounded hits. Before validity filtering: 18/27 hit. | 184.389 s and 527,971 tokens per run; 14,255,209 tokens total; $29.677447 estimate | 494 model turns; 315 shell tools |

Only 17/27 VidXP/direct-local pairs remained both condition-valid and
scorable. On those pairs, VidXP achieved 8/17 hits versus 7/17, averaged
194,499 versus 263,239 tokens, and averaged 70.045 versus 94.565 seconds.
Average Promptfoo estimates were $0.312777 versus $0.588837.
Those are diagnostics, not a product win: excluding 10 pairs can bias both
quality and efficiency. The product gate is therefore **not scored**.

The primary exclusions were seven VidXP runs that inspected source media,
one without a source job, and one whose returned evidence did not belong to
that job. Two direct-local runs read prior benchmark artifacts outside their
workspace; one overlaps a VidXP-invalid pair. Two clean-user runs reached host
developer-tool paths and three read repository or prior benchmark state; that
supporting lane does not enter the primary gate. The scorer fix
made eight legitimate agent query paraphrases valid, distinguished one
VidXP-delivered clip inspection from source-media bypass, and corrected all
five manifest durations to the indexed media values. The duration correction
restored a 4 ms end-of-video answer. Future
runs also omit the direct source path from VidXP-on instead of relying only on
post-run exclusion.

The earlier pilot remains unscored. The replacement root-denied permission
profile and preflight boundary probes were added afterward, so a new smoke must
confirm all three model conditions before the paid pilot is repeated.

Across 26 recoverable VidXP source jobs, fused retrieval at tIoU 0.5 was
6/26 at R@1 and 14/26 at R@3 and R@5. Useful candidates therefore reached the
top three more often than rank one; final ordering remains the clearest product
failure exposed by this run. Exact boundaries also remain weak. No provider,
fusion, or serving-window change was made from this result alone.

### Development smoke

The retained development run uses one
[LongVALE](https://openaccess.thecvf.com/content/CVPR2025/papers/Geng_LongVALE_Vision-Audio-Language-Event_Benchmark_Towards_Time-Aware_Omni-Modal_Perception_of_Long_Videos_CVPR_2025_paper.pdf)-derived
task with reference interval `0–6` seconds. It proves the harness and exposes
product behavior; one task is not a LongVALE score or a held-out quality
estimate.

| Evaluation | Machine | VidXP | Direct local | Clean user | Valid conclusion |
| --- | --- | --- | --- | --- | --- |
| [`eval-0eL-2026-09-05T22:40:10`](runs/eval-0eL-2026-09-05T22-40-10.json) | `mac-m2-01` | `0–12` s; hit `1`; coverage `1`; IoU `.500`; 72.888 s; 221,139 tokens; 9 turns; 6 Promptfoo-recorded tools; $0.317147 | `0–10` s; hit `1`; coverage `1`; IoU `.600`; 93.591 s; 373,984 tokens; 16 turns; 7 recorded tools; $0.755479 | `0–10` s; hit `1`; coverage `1`; IoU `.600`; 245.755 s; 860,165 tokens; 30 turns; 26 recorded tools; $1.572180 | Corrected three-condition development smoke. VidXP matched the primary result with 40.9% fewer tokens and 22.1% lower latency than direct local inspection. Product gate not scored. |

The VidXP job ranked `0–10` seconds first with action, scene, and sound support.
The agent expanded its answer to `0–12`, which accounts for the lower answer
IoU. Promptfoo supplies time, tokens, cost, recorded items, and tool types. The
report reads Codex rollout token events only for the internal model-turn count.

### Historical agent runs

All rows below predate the 2026-09-06 neutral-prompt and state-isolation fix.
The prompt named VidXP or its absence, and conditions reused one Codex home, so
their quality and efficiency deltas are retained only as debugging history.
They cannot support an ablation claim.

| Evaluation | Machine | VidXP-on | VidXP-off | Efficiency comparison | Valid conclusion |
| --- | --- | --- | --- | --- | --- |
| [`eval-2uz-2026-09-05T17:39:13`](runs/eval-2uz-2026-09-05T17-39-13.json) | `mac-m2-01` | `0–10` s; bounded hit `1`; coverage `1`; IoU `.6000`; 78.660 s; 200,142 total tokens; 52,458 uncached input; 1,636 output; 5 MCP calls; $0.384394 estimate | `0–10` s; bounded hit `1`; coverage `1`; IoU `.6000`; 90.582 s; 277,660 total tokens; 27,837 uncached input; 2,527 output; 7 shell calls, 6 through FFmpeg/ffprobe; $0.639381 estimate | VidXP used 77,518 fewer tokens, 11.922 fewer seconds, and a $0.254987 lower provider estimate; uncached input was 24,621 higher | Both found the same useful fixed window. Historical bounded-clip diagnostic only; the baseline prompt was contaminated. |
| [`eval-J6s-2026-09-01T19:30:07`](runs/eval-J6s-2026-09-01T19-30-07.json) | `mac-m2-01` | `0–8.0075` s; IoU `0.7493`; 74.552 s; 301,712 total tokens; 48,423 uncached input; 1,769 output; 6 MCP calls; $0.815355 provider estimate | `0–6.8` s; IoU `0.8824`; 112.209 s; 329,961 total tokens; 35,906 uncached input; 3,623 output; 10 media shell calls; $0.812527 estimate | VidXP used 28,249 fewer tokens and 37.657 fewer seconds, but more uncached input made its estimate $0.002828 higher. | Both found the event. Historical boundary diagnostic only; the baseline prompt was contaminated. |
| [`eval-mw5-2026-09-02T19:40:44`](runs/eval-mw5-2026-09-02T19-40-44.json) | `mac-m2-01` | `0–10` s; IoU `0.6000`; 79.647 s; 261,995 total tokens; 48,523 uncached input; 1,760 output; 7 tools, including 6 MCP calls; $0.401271 estimate | `0–6.81` s; IoU `0.8811`; 89.757 s; 313,617 total tokens; 56,950 uncached input; 3,227 output; 9 media shell calls; $0.968155 estimate | VidXP used 51,622 fewer tokens, 10.110 fewer seconds, two fewer tools, and a $0.566884 lower estimate. | Superseded global-only FineLAP diagnostic. The ten-second result rejects a global sound window as the final boundary; it does not measure current two-stage sound search. |
| [`eval-jJD-2026-09-01T17:51:57`](runs/eval-jJD-2026-09-01T17-51-57.json) | `mac-m2-01` | `64.031–75.809` s; IoU `0`; 89.030 s; 229,415 total tokens; $0.353389 estimate | `0–6.8` s; IoU `.8824`; 72.650 s; 207,110 total tokens; $0.307287 estimate | VidXP used 22,305 more tokens and 16.380 more seconds | Failed historical ranking diagnostic. It exposed the sound tokenization/integration defect later fixed in `343bd27`; it is not current product evidence. |
| [`eval-YDK-2026-09-05T20:29:45`](runs/eval-YDK-2026-09-05T20-29-45.json) | `mac-m2-01` | `0–10` s; hit `1`; IoU `.600`; 78.249 s; 273,865 total tokens; $0.751985 estimate | `0–10` s; hit `1`; IoU `.600`; 120.378 s; 244,632 total tokens; $0.398351 estimate | VidXP used 29,233 more tokens and 42.129 fewer seconds | Harness-design diagnostic only. Its tool-free third lane could not inspect media, so that lane was rejected and replaced by the clean-user bootstrap condition. |

Historical dollar values are Promptfoo's supplied provider estimates. The
report preserves them unchanged. Use them only to compare conditions using the
same pinned Promptfoo version and model configuration; they are not measured
subscription charges or invoices. Reasoning tokens are already included in
output tokens.

## Offline indexing measurements

Index construction is a separate systems benchmark because users pay it before
search while the agent comparison measures work after the index exists.

| Protocol | Measurement | Current status |
| --- | --- | --- |
| Five pilot videos totalling 914.789 seconds; scene, action, sound, and speech; prepared pinned model cache; model downloads disabled; fresh data and index directories for each repetition; sequential CLI path matching benchmark setup | Per-video import, four-modality indexing, combined time, indexing real-time factor, total wall time, and final index bytes. Report every repetition plus mean, median, sample standard deviation, minimum, and maximum. | Not run. Use `./benchmarks/codex-mcp/run indexing` for three repetitions. The command does not touch the prepared agent index and writes a path-free JSON artifact under `docs/benchmarking/runs/` for review and later linkage here. |

The first repetition may benefit less from operating-system file cache than the
later ones, so raw repetitions stay visible; averages do not erase that order
effect. This measures the existing product CLI path, including process and
model load inside each per-video index command. It excludes dataset download,
model preparation, agent inference, and search. This is resource accounting,
not a paper-derived ranking method or an accuracy score.

## Component and ranking measurements

These controls use frozen LongVALE-derived tasks and `mac-m2-01`. They make no
Codex or API calls. “Candidate recall” asks whether a usable interval exists in
the returned list; it does not mean that VidXP selected that interval.

| Experiment and research basis | Scope and cost | Result | What it establishes |
| --- | --- | --- | --- |
| `p2s_asg_vidxp_v1`; [Point-to-Span](https://arxiv.org/abs/2512.10363), Section 3.1 | One development task; saved score curves; no model calls | Previous union IoU `0.7493`; adapted interval `0.64–6.72` s and IoU `0.7976`; direct-inspection IoU `0.8824` | The adaptive sound span helped, but the partial adaptation produced no scene or action span and remained below direct inspection. It is concluded, not adopted. |
| `videoprism_overlap_control_v1`; [CTAP](https://openaccess.thecvf.com/content_ECCV_2018/html/Jiyang_Gao_CTAP_Complementary_Temporal_ECCV_2018_paper.html) and [long-video guidance](https://openaccess.thecvf.com/content/ICCV2023/html/Barrios_Localizing_Moments_in_Long_Video_Via_Multimodal_Guidance_ICCV_2023_paper.html) motivate candidate coverage | Five action tasks; normal 79 records versus 307 four-second records; five text embeddings; fine index took 1,175.579 s and wrote 5,966,316 bytes | Eight-second top-1 mean IoU `0.0680`, R@1 at tIoU 0.5 `0`; four-second top-1 mean IoU `0.1297`, R@1 at tIoU 0.5 `.20`, top-3 candidate recall `.40`, full-list recall `.60`; top-three coarse gating reduced full-list recall to `.40` | Overlap improves candidate availability, but raw VideoPrism similarity and the tested gate do not rank it reliably. CTAP's learned proposal ranking and boundary adjustment were not implemented. |
| `diwan_shotdetect_siglip2_v1`; [Off-the-Shelf VMR](https://proceedings.mlr.press/v203/diwan23a.html) | Eight tasks; 16 text embeddings; 46.744 s probe generation; 16.923 s shot detection; no model calls or index writes for detection | Development shot IoU `0.8902`. Held out: current union mean IoU `0.0418`; best-shot oracle `.5219`; on six scene-comparable tasks, scene ranking `.2841` versus proposal RRF `.1175` | Shot boundaries can supply useful candidates. VidXP's proposal RRF harmed ranking; five tasks were boundary-limited and three ranking-limited at tIoU 0.5. The paper's CLIP plus SimpleWatershed pipeline was not reproduced. |
| `manual_modality_query_ceiling_v1`; query decomposition is compared with, not claimed from, [Zero-Shot VMR](https://openaccess.thecvf.com/content/WACV2024/html/Luo_Zero-Shot_Video_Moment_Retrieval_From_Frozen_Vision-Language_Models_WACV_2024_paper.html) | Eight tasks; 16 task-modality pairs; 32 text embeddings; 13.590 s | Target overlap in top 3 changed `7/16` to `8/16`; best-boundary record in top 3 changed `4/16` to `7/16`; nine overlap ranks improved and two worsened | Manual modality wording is an upper-bound control, not the paper's full method. Mixed results reject mandatory rewriting. |
| `finelap_separate_streams_v1`; [FineLAP](https://aclanthology.org/2026.acl-long.473/), Sections 3.2–3.3 | Four sound tasks within the preceding query control | A target appeared in a top-three list on `0/4` tasks when global and dense records were mixed and `3/4` when the streams were ranked separately | The original mixed ranking was invalid. The result supports separate representation paths, not VidXP's final global-then-local selector. |
| `finelap_two_stage_runtime_2026-09-03`; [FineLAP](https://aclanthology.org/2026.acl-long.473/) representations with VidXP orchestration | One real Apple Silicon query through FineLAP, Chroma, the application, fusion, and JSON output | Dense result `1.60–2.08` s with parent context `0–10` s | Real-path smoke only. It proves the current selector executes; it supplies no held-out IoU or comparative quality evidence. |
| `finelap-two-stage-held-out@eae7000`; [FineLAP](https://aclanthology.org/2026.acl-long.473/), Sections 3.2–3.3, plus VidXP's selector | Four designated intervals; full frozen application queries; top 3; eight local text embeddings including diagnostic duplication; 5.172 s total | Global gate coverage `2/4`; final activation top-1 and top-3 coverage `0/4`; full gated activation coverage `2/4`; final mean IoU `0`; R@1 at tIoU 0.3/0.5/0.7 all `0`; surviving target ranks `132` and `63` | Exact diagnostic retained, but phone and engine invalidate it as a provider decision. It does not decide whether the paired multimodal run can proceed. |
| `candidate-depth-fusion-control-v1`; [RRF](https://doi.org/10.1145/1571941.1572114) ranking over current VidXP temporal groups | All ten frozen collective tasks; saved full-query modality rankings; depths 1, 3, 5, 10, 20, 50, 100, 250, 500, 1,000, and all; final depth 10; no model or API calls | From depth 3 to 20, board R@3 at tIoU 0.5 rose `.30` to `.40` and output R@10 rose `.30` to `.40`, while R@1 stayed `.20`. At depth 100, R@1 fell to `0`; at full depth, every top result spanned nearly the whole video and all threshold rates were `0`. | Early truncation hides usable evidence, but a larger fixed depth is not the fix. Transitive overlap grouping turns denser input into video-length components. Candidate generation must be separated from final ranking before candidate depth can be selected. |
| `candidate-depth-direct-overlap-control-v2`; [RRF](https://doi.org/10.1145/1571941.1572114) over VidXP's corrected bounded candidates | `mac-m2-01`; the same ten frozen tasks and saved rankings; identical depth sweep; final depth 10; no model or API calls | Depths 100 through all produced identical rates. At full depth, R@1/R@3/R@5/R@10 at tIoU 0.5 were `.10/.10/.20/.20`; no top result expanded to the full video. | Direct overlap fixes the transitive-union failure. Low R@5 remains attributable to provider ordering and source-window boundaries, not depth collapse. |
| `pe-a-frame-small-mac-diagnostic`; [PE-AV](https://arxiv.org/abs/2512.19687), PE-A-Frame Small `e5fc71c1f0be50279f52f292390b589780079e13` | `mac-m2-01`; official Transformers implementation; F32 CPU; official threshold `0.3`; no API calls. One complete 73.14-second phone-ring track plus four label-centered clips. | Full track: 244.35 s, 4.30 GiB peak RSS, 125 predicted fragments, target miss. Target-aware clips: full-query mean best-span IoU `0.1654` and target score above surrounding audio `1/4`; sound-only mean `0.1151` and `0/4`. Best per-task full-query IoU: siren `0.0317`, engine `0.4615`, phone `0`, drumbeat `0.1682`. | Inconclusive for provider selection because two sound labels were invalid. Retained as a runtime and failure diagnostic; the AEGBench row below supersedes it for selection. |
| `aegbench-sound-seed42-n50`; [AEGBench](https://huggingface.co/datasets/zihan-audio/AEGBench) `49a1d919`, [PE-A-Frame Small](https://huggingface.co/facebook/pe-a-frame-small) `e5fc71c`, FineLAP `b419aa2` | `mac-m2-01`; 50 recordings sampled from all 3,425 manifest rows with seed 42; 149 categories with annotated intervals; two categories without intervals excluded explicitly; 613.43 seconds of audio; no API calls or test-set threshold tuning | PE-A/FineLAP frame AUROC `.8614/.8401`; frame AP `.7616/.7484`; top point inside an event `.7651/.7315`; default-threshold mean IoU `.5226/.2924`; R-IoU@0.5 `.5099/.2802`. Inference `183.30/17.98` s; real-time factor `.2988/.0293`; peak RSS `5.30/1.59` GB. | PE-A-Frame Small selected for sound localization because it wins every quality measure while remaining faster than playback. This subset decides the candidate, not a full AEGBench score or long-audio claim. |
| `pe-a-product-section-smoke-2026-09-05`; [PE-A-Frame Small](https://huggingface.co/facebook/pe-a-frame-small) `e5fc71c` | `mac-m2-01`; 75.81-second LongVALE development video; product decoder, model runtime, Chroma inner-product index, frame de-duplication, and search; no API calls | A 60-second-section control took `198.651` s. Ten-second sections took `23.356` s at zero overlap, `22.156` s at two seconds, and `33.564` s at five seconds; warmed-model timings are not a formal speed comparison. Every run stored 1,896 unique frames. Two-second overlap ranked the labelled opening event in `0–10` s and ending bell in `70–80` s. | Reject 60-second sections on this Mac. Ten seconds is the measured resource choice; two seconds is the smallest tested nonzero overlap and five seconds added cost without changing the checked results. The ten-second evidence window is a playable context unit, not a boundary claim. This one-video smoke is not general retrieval accuracy; the long-audio gate remains required. |
| `kinetics-mini-videoprism-2026-09-05`; [Kinetics](https://arxiv.org/abs/1705.06950) [five-class derivative](https://huggingface.co/datasets/nateraw/kinetics-mini) `9f4ed381`; VideoPrism `fb6de9f` | `mac-m2-01`; 50 ten-second validation videos; VidXP's 2 fps/16-frame records; five direct action prompts; no API calls | Top-1 `1.00` overall and for every class; 390.49 s total, 7.81 s/video. PE-AV Small 16-frame `9f888ee` classified one archery smoke correctly but took 13.36 s; its weights are 3,388,082,648 bytes. | Keep VideoPrism. The gate shows that basic action recognition works; it says nothing about exact long-video location. PE-AV supplies no interval head and offered no measurable quality headroom here. |
| `flexsed-mac-held-out`; [FlexSED](https://arxiv.org/abs/2509.18606) detector `eefe52b7ad686a9bc9f1f5dd0803e2c52171e128`, LAION CLAP `8fa0f1c6d0433df6e97c127f64b2a1d6c0dcda8a` | `mac-m2-01`; released non-overlapping ten-second path; 63 detector calls over 616.7 seconds of unique audio; full and sound-only wording; no API calls or tuned settings | Load `1.265` s; inference `10.854` s; peak RSS `1.57` GiB. Designated target score beat all surrounding frames on `0/4` full and `0/4` sound-only queries. Mean target-best frame percentile was `0.7962` full and `0.7439` sound-only. Published processing produced one designated-target overlap, engine at about `0.045` IoU. | Runtime passes, but the overall quality rate is invalid because phone is silent and engine has repeated valid matches. FlexSED missed both unique valid cases and is not selected; overlap cannot repair those raw misses. |
| `dasm-release-compatibility-2026-09-05`; [DASM](https://arxiv.org/abs/2507.16343), Transformer4SED `c3e883d0fbeaf7031b467d45a3c46a88a76c00b6` | `mac-m2-01`; read-only inspection of official source, inference notebook, requirements, and 636 MB model-hub tree; no API calls | Text inference sets `device = 'cuda'`, requires an external MGA-CLAP checkout and checkpoint, and uses hard-coded local paths. The Transformer4SED repository has no software license. | Blocked before execution; no quality or runtime score. MIT metadata on the model hub does not grant a license to copy the separate source implementation. |
| `wstag-audiocaps-v2-mac-held-out`; [WSTAG](https://arxiv.org/abs/2401.02584), model `c1ede4afca77acb67bbd20e48e3fc4657b96666a`, LAION CLAP `365dea6ef167def6676140ed93bbc43f84dabb28` | `mac-m2-01`; author-recommended post-paper 131.96M-parameter model; exact 528,030,960-byte weights; three audible designated intervals, full and sound-only wording; six whole-track CPU forwards over 1,679.9 input seconds; no API calls or tuned settings | Load `0.811` s from cache; inference `25.82` s; individual 247–296 s tracks `3.42–5.02` s; peak RSS `4.15` GiB. Designated target wins were `0/3` for either wording; mean target-best percentile `0.8688` full and `0.8985` sound-only. Designated-target IoU was zero at the released `0.5` threshold. The engine top at `242.22` s is inside another annotated rev interval (`241.760–243.554` s). | Runtime passes. WSTAG missed the two unique valid cases and is not selected, but no overall provider score is claimed. The hub's advertised AutoModel path is broken; the diagnostic loaded the same class and exact weights with zero checkpoint mismatches. |
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
- the action, proposal, query, sound, candidate-depth, and Point-to-Span controls under
  `benchmarks/codex-mcp/scripts/`;
- the selected, sanitized, importable [Promptfoo run exports](runs/), including
  the corrected smoke and historical diagnostics that changed direction;
- [current result interpretation](results.md), [paper validation](paper_validation.md),
  and [published comparison results](published_results.md).

Generated databases, media, indexes, model weights, raw Codex response bodies,
session IDs, secrets, and personal paths are not committed. The retained
Promptfoo exports preserve the remaining configuration, responses, scores,
usage, traces, and tool items needed to audit selected agent runs.

## Measurements still required

- Rebuild the sound index and run the PE-A-Frame long-audio product gate. The
  provider and bounded section path are implemented, but the one-video smoke
  does not validate hour-long or fused retrieval.
- Run the three-condition smoke under the root-denied permission profiles, then
  rerun the 81-run pilot; the first pilot is retained but unscored.
- Run the isolated three-repetition indexing benchmark and link its reviewed
  JSON artifact from the offline-indexing table above.
- Produce full-corpus DiDeMo and HiREST results for the current providers.
- Add model revisions, peak memory, model-call counts, and raw-prediction
  identity to future generated run manifests. New agent exports now carry the
  stable machine ID; do not infer fields missing from historical execution.
