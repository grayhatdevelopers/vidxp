# Evidence retrieval direction

Collection index: [Benchmarking research](README.md)

Status: Current product and evaluation decision

Last verified: 2026-09-06

The [research adoption record](research_adoption.md) is the source of truth for
paper-derived product behavior. The [paper inventory](research_papers.md)
records relevant work without implying that VidXP adopts it.

## Product target

VidXP gives an AI agent a compact, inspectable view of a video library: matching
speech, sounds, frames, action clips, timestamps, and playable evidence. The
agent remains responsible for interpreting that evidence and answering the
user. VidXP does not need to replace the agent with one all-in-one video model.

A product-level comparison succeeds when VidXP matches or improves the agent's
bounded-chunk hit rate while using fewer total tokens. The current benchmark
targets a 10-second evidence clip, accepts 8–12 seconds, and requires at least
half of the event available to one target-size clip. This VidXP serving rule
rejects both blink-length and whole-video answers. Report cached and uncached
input, output, reasoning, time, cost, and calls alongside it. Temporal IoU and
threshold recall remain secondary exact-boundary diagnostics and an explicit
future research limitation.

## Providers used in the current agent run

| Lane | Selection | Evidence and limit |
| --- | --- | --- |
| Speech | Keep faster-whisper plus Qwen3 Embedding | The real runtime works; the complete HiREST ranking run and a transcription WER gate remain pending. |
| Scene | Keep SigLIP 2 | The real runtime works; the complete DiDeMo current-provider run remains pending. |
| Action | Keep VideoPrism LvT | It classified all 50 videos in the frozen five-class Kinetics-mini gate correctly through VidXP's current 2 fps/16-frame records. This establishes basic recognition, not temporal localization. |
| Sound localization | Use PE-A-Frame Small; keep FineLAP only as a benchmark control | On the identical 149-query AEGBench subset, PE-A improved frame AUROC from `.8401` to `.8614`, frame average precision from `.7484` to `.7616`, top-point accuracy from `.7315` to `.7651`, and default-threshold mean IoU from `.2924` to `.5226`. It was about 10.2 times slower, but still processed audio 3.35 times faster than playback on `mac-m2-01`. |

This selects providers; it is not a full product score. The isolated agent
pilot has now run with this stack. Replacing VideoPrism with another global
clip-similarity model alone would not fix temporal localization. PE-AV has no
interval head, uses a 3.39 GB checkpoint, and its one-video direct-forward
smoke took 13.36 seconds versus VideoPrism's 7.81-second mean over the 50-video
gate.

## What the product can claim now

- The intended answer is a ranked list of useful, playable evidence chunks,
  normally about ten seconds each. It is not a promise to cut the event at its
  exact first and last frame.
- PE-A-Frame is the integrated sound-localization provider. On the frozen
  subset, it put its highest-scoring 40 ms frame inside a labelled event for
  `76.5%` of queries and reached `.523` mean IoU at its released threshold.
- VideoPrism remains the action provider. Its perfect result on five easy
  Kinetics classes shows that the model and VidXP preprocessing recognize broad
  actions; it does not show that long-video moments are ranked or trimmed well.
- On the selected nine-task pilot, the VidXP agent returned a qualifying clip
  on `15/27` repeated runs (`55.6%`), while its visible MCP top three contained
  one on `19/27` (`70.4%`). Direct local inspection scored `18/27` (`66.7%`).
  These are pilot rates over nine repeated tasks, not general product accuracy.
- The same pilot measured 20.6% fewer agent tokens, 18.3% lower latency, and a
  25.9% lower Promptfoo comparison-cost estimate for VidXP than direct local
  inspection. VidXP won 20/27 matched token comparisons and 19/27 latency and
  cost comparisons. This establishes an average efficiency gain under the
  fixed protocol, not an API bill or an accuracy win.

On this CPU-only Mac, PE-A processed 613.43 seconds of audio in about 183
seconds, so a linear inference-only estimate is roughly 18 minutes per hour of
audio. VideoPrism averaged 7.81 seconds per ten-second Kinetics clip, or roughly
47 minutes per hour at the same sampling policy. These are lane estimates, not
an end-to-end indexing promise; decoding, speech, scene indexing, storage, and
long-video chunk overlap still need an hour-video run.

## Current product path

VidXP builds reusable local indexes for separate evidence types:

- faster-whisper and Qwen3 Embedding produce timestamped speech evidence;
- PE-A-Frame Small produces frame-ranked environmental-sound evidence;
- SigLIP 2 retrieves sampled visual frames;
- VideoPrism ranks fixed multi-frame clips by global text-video similarity; and
- reciprocal rank fusion ranks bounded candidates. Each candidate keeps one
  anchor hit and at most one directly overlapping hit from each other modality.

This modular path remains the product control. No current evidence requires
replacing every provider or moving to a single trained temporal model.

VideoPrism's published action results do not validate this fixed-window
ranking as temporal action localization. A direct conformance check found that
the pinned Transformers port matches Google's official Flax checkpoint; the
remaining action failure is therefore in the product's global-similarity
ranking design, not the converted model weights.

VidXP already has an optional local SLM path: `query_video` can use the
self-hosted Ollama `qwen3.5:4b-q4_K_M` model for typed query planning and
grounded answer synthesis, with deterministic evidence fallback. This is a
VidXP product option, not a paper-derived requirement, and it has not been run
through the agent-ablation tasks. Evaluate it as a separate local-answer lane,
not as a retroactive replacement for the Codex MCP condition.

The paper-facing SLM condition is narrower and distinct: Promptfoo gives the
same managed model the shipped skill and five required MCP tools, then applies
the same three-candidate schema and deterministic scorers used for Codex. The
provider reports Ollama input/output tokens, model requests, and MCP calls.
Memory, energy, and local compute cost remain unmeasured rather than being
treated as zero. This tests whether VidXP can serve a local agent without
external model exposure; it does not claim that the harness agent is already a
shipped VidXP UI feature or that local inference is costless.

## Confirmed limits and decisions

### Keep FineLAP as a historical benchmark control

Xiquan Li et al., [“FineLAP: Taming Heterogeneous Supervision for Fine-grained
Language-Audio Pretraining”](https://aclanthology.org/2026.acl-long.473/), ACL
2026, Sections 3.2–3.3, trains separate global and local audio projections for
clip-level and frame-level supervision. VidXP previously stored both outputs in
one collection and ranked the raw records together.

That integration was invalid: the two score lists did not form one calibrated
ranking. On four held-out sound tasks, the mixed top three contained target
evidence on 0/4 tasks; querying the representations separately did so on 3/4.

FineLAP supports separating the global and local outputs. It does not establish
VidXP's global top-three gate followed by one pooled activation ranking over
those windows. Section 3.3 trains local scores against short event phrases and
frame labels inside a clip; the paper's Limitations section explicitly leaves
long-form audio and temporally enhanced audio-text retrieval unevaluated. The
VidXP selector returned no final top-three overlap against the four designated
intervals and missed the two unambiguous cases. The four-task rate is not a
valid provider score because one reference is silent and another query has
multiple correct occurrences. Treat the selector as historical and unvalidated,
not adopted or conclusively rejected. It remains reproducible in the benchmark
adapter. Product index schema 8 replaces both representations with PE-A frames,
so older indexes must be rebuilt.

The sound provider must localize a free-form acoustic description, including
short environmental events, and return every useful occurrence. It does not
need to interpret visual or speech-only clauses; those belong to the other
providers and the agent. Two research tasks are therefore relevant:

- audio moment retrieval tests sentence-to-interval retrieval over minutes of
  audio; and
- open-vocabulary sound-event grounding tests fine event boundaries and
  repeated or overlapping occurrences.

These are sound-provider diagnostics, not substitutes for LongVALE's combined
task. Product search passes the same full query to each requested modality and
applies reciprocal rank fusion. A hit seeds a bounded candidate and can receive
support only from the best directly overlapping hit in each other modality.
Indirect overlap cannot join distant moments, and hits from the same modality
remain separate candidates. A sound result can therefore support a visual
match without merging with another sound match elsewhere in the video.

The API now separates candidate collection from final output. `top_k` limits
only the fused results returned to the caller. `candidate_top_k` independently
limits each modality to 100 hits by default; MCP evidence delivery then shows
three fused candidates by default. Cormack, Clarke, and Buettcher's RRF paper
supports the rank formula and its `k = 60` constant. It does not prescribe
either output limit. The candidate budget is a VidXP resource cap: 100 matched
exhaustive input on the corrected ten-task control, but is not a general
accuracy optimum.

Fresh fused queries use the `rrf_v2` identity. Existing indexes remain valid,
and stored `connected_intervals` provenance remains readable.

The original saved-ranking depth control confirmed that candidate depth could
not be selected while transitive overlap corrupted the output. At full depth,
every top result covered nearly its entire video. After direct-overlap fusion
replaced that grouping, depths 100 through all produced identical metrics
instead of collapsing. The corrected run still reached only `0.20` R@5 at
tIoU 0.5, so it fixes candidate identity but not provider ranking or boundary
errors. Neither curve selects a serving depth.

The model papers keep this seam simpler than the current implementation.
FineLAP exposes separate clip- and frame-level representations; VideoPrism is a
frozen video encoder; and SigLIP 2 is an image-text encoder whose localization
results use downstream heads. None defines temporal rank fusion. LongVALE
Section 3.2 first builds semantically coherent visual and audio events, then
combines those event boundaries while preserving audio integrity. VidXP's
direct-overlap rule prevents false video-length unions, but model-specific
event proposals remain the next boundary-quality seam.

[DCASE 2026 Task 6](https://dcase.community/challenge2026/task-audio-moment-retrieval-from-long-audio-results)
is the strongest direct long-audio evidence found. Its official
MS-CLAP/QD-DETR baseline scored 13.56 R1@0.7; a 211.87M-parameter
M2D-CLAP/CG-DETR entry reached 48.59, but public code and weights for that entry
were not verified. The released CASTELLA/Lighthouse control reached only 20.3
R1@0.7, is weak on sub-ten-second moments, truncates audio-feature sequences
beyond 300 seconds, and conflicts with the managed runtime. A separate runtime
would reproduce that baseline; it has no demonstrated product advantage.

The first executable dense-sound candidate tested was Meta's
[PE-A-Frame Small](https://huggingface.co/facebook/pe-a-frame-small), from Vyas
et al., [“Pushing the Frontier of Audiovisual Perception with Large-Scale
Multimodal Correspondence Learning”](https://arxiv.org/abs/2512.19687). It
accepts free-form audio descriptions and emits frame scores and multiple spans
at about 40 ms resolution. The Apache-2.0 checkpoint has 450M parameters and a
1,758,756,416-byte F32 weight file. Its official localization AUROC is
0.83–0.96 across the published event-localization sets; AUROC is not interval
IoU and does not establish VidXP accuracy. The installed Transformers runtime
has the official PE-Audio classes, avoiding the source repository's optional
`xformers` path.

The pinned Small checkpoint failed the initial flawed Mac product diagnostic. A
complete 73.14-second soundtrack took 244.35 seconds on CPU and peaked at 4.30
GiB RSS. The full query
missed the phone-ring target and produced 125 fragments at the official 0.3
threshold. On target-aware clips, which test recognition but not retrieval, the
mean best-span IoU was 0.1654 for full queries and 0.1151 for sound-only phrases;
the target outscored surrounding audio on only one of four full-query cases and
none of the sound-only cases. Threshold tuning cannot fix a target whose score
is below the surrounding audio. PE-A-Frame Small was therefore not adopted from
that run. The diagnostic was not a native provider benchmark: two of its four
labels were unsuitable for sound-only scoring.

The subsequent frozen AEGBench comparison supplied the missing valid gate. It
used 50 recordings sampled with seed 42 from the 3,425-row manifest, 149
categories with annotated intervals, and every repeated interval. Two manifest
categories with no interval were excluded explicitly. PE-A-Frame Small beat
FineLAP on every ranking and default-threshold interval measure in the selection
table while remaining faster than playback on the CPU-only Mac. This selects
PE-A-Frame Small for sound localization. It does not select PE-AV for
action/video, and it is not a full AEGBench leaderboard result.

For long media, VidXP defaults to ten-second inference sections with a
two-second overlap, assigns each overlap at its midpoint, and maps the retained
40 ms frames to global timestamps. Search ranks with the checkpoint's dot
product and returns the best frame per fixed ten-second evidence window. The
values are configurable deployment defaults, not PE-A-Frame claims or measured
accuracy optima. Keep distinct repeated events separate.

### Treat fused intervals as bounded evidence candidates

The current fusion anchors each candidate to one ranked hit. It adds at most
the best directly overlapping hit from each other modality and returns the
smallest interval containing that evidence. It never merges same-modality hits
or follows an overlap chain into another moment. The RRF formula and `k = 60`
come from Gordon Cormack, Charles Clarke, and Stefan Buettcher,
[“Reciprocal Rank Fusion Outperforms Condorcet and Individual Rank Learning
Methods”](https://doi.org/10.1145/1571941.1572114), SIGIR 2009. Candidate
construction and interval boundaries remain VidXP engineering.

The direct-overlap correction removes video-length chains, but its ten-task
replay reached only `0.20` R@5 at tIoU 0.5. Several correct action regions
remain eight-second windows around two-second references, and some target
evidence is ranked far below five by its provider. Candidate construction no
longer corrupts separate moments, but precise boundaries and ordering still
depend on the modality providers.

No replacement boundary model has been selected. The overlapping-action
control did produce a near-target shorter record, but the then-existing union
joined it to its neighbors. A held-out follow-up then tested a simple
coarse-to-fine path without union. Fine candidate recall improved, but the
coarse gate and similarity ranking missed most answers, so that path is not a
product fix.
Point-to-Span and shot-proposal fusion also remain concluded benchmark controls.
Their exact results and deviations are recorded in the
[research adoption record](research_adoption.md).

## Next actions

### Sound

FlexSED's pinned released path was also tested. It processed
616.7 seconds of unique audio in 10.85 seconds and peaked at 1.57 GiB RSS, so
the runtime fits. Quality did not: target audio outscored the rest of its
soundtrack on 0/4 full queries and 0/4 sound-only phrases. At the published
0.5 threshold with a nine-frame median, only the engine case overlapped its
reference, at about 0.045 IoU. Overlap cannot repair raw target scores below
unrelated regions. It also missed the two unambiguous audible targets, siren
and drumbeat. Do not select it from this result, but do not report `0/4` as a
valid provider-quality estimate: the phone reference is invalid and the engine
query has multiple correct occurrences.

The reference-audio check found one invalid component case. The annotated
telephone-ring interval has `-91.75 dBFS` RMS and `-78.27 dBFS` peak signal;
the preceding five seconds are `-45.85 dBFS`. The local MP4 is byte-identical
to the downloaded LongVALE archive, so this is not local corruption. Quarantine
that task from sound-only scoring pending human review; do not silently remove
it from the multimodal pilot.

The engine task exposes a separate protocol error. Its sound-only phrase can
correctly match several engine-rev occurrences. WSTAG's top frame at 242.22
seconds falls inside LongVALE's separate 241.760–243.554-second annotation for
the Cayenne engine rumbling and revving. The scorer nevertheless marks it wrong
because it accepts only 25.560–27.560 seconds, where the multimodal query also
specifies a gesturing driver. A sound provider cannot use that visual clause.
Sound-component evaluation must label every acoustically matching occurrence;
the existing single reference remains valid only for the full multimodal
fusion task.

DASM is not an executable Mac candidate. The official text-query notebook at
Transformer4SED revision `c3e883d0fbeaf7031b467d45a3c46a88a76c00b6`
hard-codes CUDA and a local checkout, and requires a separate MGA-CLAP
repository and checkpoint. Its model hub publishes 636 MB of DASM artifacts
under MIT metadata, but the source repository contains no software license.
Do not copy, port, or benchmark that implementation unless the authors clarify
the code license and provide a supported non-CUDA path.

Xu et al., [“Towards Weakly Supervised Text-to-Audio
Grounding”](https://arxiv.org/abs/2401.02584), IEEE Transactions on Multimedia
2024, provides the next lawful CPU path. The authors recommend a newer
AudioCaps-v2/LAION-CLAP Hugging Face model rather than the paper's original
checkpoint. Against the current single-reference control, neither the full
query nor the sound phrase ranked the designated target first on the three
audible tasks.
Mean target-best frame percentile was 0.8688 and 0.8985 respectively, but the
official `0.5` inference threshold returned no target-overlapping interval, so
IoU was zero on all six passes. Six CPU forwards over 1,679.9 seconds of input
audio took 25.82 seconds; each 247–296-second recording took 3.42–5.02 seconds,
and peak process RSS was 4.15 GiB. WSTAG missed both unambiguous cases at that
threshold; the engine top result was a separate valid occurrence. It is not
selected, but the flawed three-task control cannot provide a final quality
estimate. Its hub metadata is
also missing the `AutoModel` mapping advertised by its README; the local test
loaded the same published class and exact weights directly with zero checkpoint
mismatches.

Three stronger-looking releases do not satisfy the product gate:

- Wu et al., [FLAM](https://arxiv.org/abs/2505.05335), ICML 2025, is the closest
  compact technical fit, but OpenFLAM is non-commercial and its public model is
  not the internal model used for the paper's reported results.
- Sun et al., [SpotSound](https://arxiv.org/abs/2604.13023), ACM MM 2026, directly
  trains short-event timestamp grounding, but it is a LoRA over the 8B
  Audio Flamingo 3 base, whose license is non-commercial and whose supported
  runtime is Linux/CUDA.
- Wang et al., [TimeAudio](https://arxiv.org/abs/2511.11039), 2025, uses a
  Vicuna-7B stack and documents more than 40 GB of GPU memory for inference.

Those candidates supplied no better distributable Mac path. The later valid
AEGBench comparison selected PE-A-Frame Small, which is now integrated. FineLAP
remains only as the recorded comparison control. Do not build a separate
DCASE/Lighthouse runtime unless a reproducibility comparison is explicitly
needed.

### Action

Do not tune fusion, window overlap, or query wording again for this failure.
The held-out comparison already showed that useful fine windows exist but raw
VideoPrism similarity ranks most of them too low.

The replacement boundary is now explicit: keep VidXP's action API and reusable
index, but replace global clip ranking with a trained temporal grounder that
consumes a sequence of visual features and predicts intervals. An et al.,
[HieraMamba](https://openaccess.thecvf.com/content/CVPR2026/html/An_HieraMamba_Video_Temporal_Grounding_via_Hierarchical_Anchor-Mamba_Pooling_CVPR_2026_paper.html),
CVPR 2026, establishes the long-video multi-scale grounding design. An, Jain,
and Grauman, [UniversalVTG](https://arxiv.org/abs/2604.08522), 2026, adds one
cross-domain checkpoint and is the closest technical product candidate.

Neither release can be adopted unchanged: both depend on a CUDA-oriented Mamba
stack, and the checked repositories do not provide a top-level product license.
The next implementation task is therefore a bounded compatibility decision:
confirm a lawful checkpoint and a CPU or Apple-Silicon runtime for that exact
grounder. If either requirement fails, reject it; Lighthouse's 150-second video
encoder limit is benchmark context, not a fallback for the sound provider. Do
not change product ranking until one candidate passes that gate on the frozen
action tasks.
