# Benchmark results

This page answers three questions:

1. What did VidXP score?
2. What do the measurements mean?
3. What can we honestly conclude from them?

Agent run artifacts and machine profiles are linked from the
[metric database](metric_database.md). Dataset hashes, commands, and evaluator
behavior remain in the [adapter validation ledger](adapter_validation.md).

## Evidence at a glance

| Generation | Benchmark | Evaluated data | Result | What it supports |
|---|---|---|---|---|
| Legacy full | DiDeMo | Official test: 4,021 searches over 1,037 videos | Rank@1 **20.19%**, Rank@5 **55.71%**, mean IoU **34.60%** | Full visual baseline with one documented replacement for a corrupt official media object |
| Legacy full | HiREST | Official validation: 193 known-video searches | R@0.5 **78.24%**, R@0.7 **44.56%** | Validation baseline; the 0.8 prediction-window fraction was selected on this same validation set |
| Legacy full | HiREST | Released test: 776 known-video searches | Predictions generated, not scored | Public test boundaries are placeholders, so local scoring would be meaningless |
| Current smoke | DiDeMo | Official test annotation index `0`; one video | Rank@1 **0**, Rank@5 **1**, mean IoU **0** | Real SigLIP2 execution, serialization, and official-evaluator check only |
| Current smoke | HiREST | Two declared validation pairs over two videos | R@0.5 **50**, R@0.7 **50** | Real Qwen3 execution, multi-video storage, filtered search, serialization, and official-evaluator check only |
| Current component gate | Kinetics-mini | 50 ten-second videos over five action classes | VideoPrism top-1 **50/50** | Broad-action recognition works; long-video ranking and boundaries are not measured |
| Current component gate | AEGBench frozen subset | 50 recordings; 149 annotated sound queries | PE-A/FineLAP top-point **76.5%/73.2%**; mean IoU **.523/.292** | Select PE-A-Frame Small for sound localization |
| Current product smoke | PE-A bounded sections | One 75.81-second development video; two known sound queries | 1,896 unique frames; both target ten-second windows ranked first; **22.156 s** indexing after model load | Product decoder/runtime/storage/search integration works; long-audio quality is still unmeasured |
| Agent development smoke | Codex MCP ablation | LongVALE-derived task `ZYT-rain-wind-engine`; neutral prompt and three isolated conditions | Every condition achieved bounded-chunk hit **1** and coverage **1**. Against direct local inspection, VidXP used **40.9%** fewer tokens and finished **22.1%** faster. | Corrected harness smoke only; one development task is not a product gate or held-out result. |
| Global-only sound diagnostic | Codex MCP ablation | Same development task after filtering sound search to global clips | VidXP-on IoU **0.6000**; VidXP-off IoU **0.8811** | Same answer content with 16.5% fewer VidXP tokens and 11.3% lower latency, but the ten-second sound clip worsened the endpoint |

The current-provider rows are deliberately tiny regression runs. Their
percentages are not quality estimates and must not be compared with the full
legacy rows. The two component gates make provider decisions only. A current
full-corpus or whole-product score has not been run.

## Codex MCP development smoke

Evaluation
[`eval-0eL-2026-09-05T22:40:10`](runs/eval-0eL-2026-09-05T22-40-10.json)
is the first corrected smoke. It uses
one neutral prompt, separate condition homes, and the practical-clip contract.

| Condition | Result | Time | Total / uncached / output tokens | Turns / items / tools | Promptfoo cost |
| --- | --- | ---: | --- | --- | ---: |
| VidXP | `0–12` s; hit `1`; coverage `1`; IoU `.500` | 72.888 s | 221,139 / 33,975 / 1,820 | 9 / 7 / 6; 5 MCP | $0.317147 |
| Direct local | `0–10` s; hit `1`; coverage `1`; IoU `.600` | 93.591 s | 373,984 / 28,323 / 2,877 | 16 / 9 / 7; 7 shell | $0.755479 |
| Clean user | `0–10` s; hit `1`; coverage `1`; IoU `.600` | 245.755 s | 860,165 / 47,247 / 6,518 | 30 / 31 / 26; 26 shell | $1.572180 |

VidXP matched direct local inspection on the primary metric with 152,845 fewer
tokens and 20.703 seconds lower latency. Its saved top fused result was `0–10`
seconds with action, scene, and sound support. The agent expanded the returned
clip to `0–12`, so answer IoU fell from the retrieval result's `.600` to `.500`.
The clean-user agent began without third-party media tools and installed its own
workspace-local FFmpeg package. This confirms the condition works; its setup
strategy is agent behavior, not a prescribed harness path.

All three assertions passed. The report correctly leaves the product gate
unscored because a one-task development smoke cannot establish comparative
quality. A per-run workspace reset was added afterward so repeated pilot cases
cannot inherit files or installed tools; that isolation hook is unit- and
configuration-validated but was not exercised by this saved smoke.

### Historical development runs

The runs below predate the neutral prompt and separate condition homes. Their
retrieval traces remain useful, but their condition deltas are invalid.

Evaluation
[`eval-2uz-2026-09-05T17:39:13`](runs/eval-2uz-2026-09-05T17-39-13.json)
asked both conditions for an 8–12
second practical clip around the `0–6` second rain, wind, and engine event.

| Condition | Result | Time | Token usage | Tools | Promptfoo cost |
| --- | --- | ---: | --- | --- | ---: |
| VidXP-on | `0–10` s; bounded hit `1`; coverage `1`; IoU `.6000` | 78.660 s | 200,142 total; 198,506 input; 146,048 cached; 52,458 uncached; 1,636 output; 490 reasoning | one skill read; five MCP calls; no media-shell calls | $0.384394 |
| VidXP-off | `0–10` s; bounded hit `1`; coverage `1`; IoU `.6000` | 90.582 s | 277,660 total; 275,133 input; 247,296 cached; 27,837 uncached; 2,527 output; 1,100 reasoning | seven shell calls, including six FFmpeg/ffprobe calls | $0.639381 |

The raw run recorded 77,518 fewer VidXP tokens and 11.922 seconds lower latency,
but the tool-aware prompt means those deltas are not an ablation result. The
durable job
ranked `0–10` seconds first with action, scene, and sound support. This confirms
the retrieval path on one development query; it does not establish comparative
efficiency or held-out accuracy.

The two older runs below used the superseded exact-interval prompt. Their raw
measurements are retained rather than silently rescored.

[`eval-jJD-2026-09-01T17:51:57`](runs/eval-jJD-2026-09-01T17-51-57.json)
returned `64.031–75.809` seconds with VidXP and `0–6.8` through direct
inspection. It exposed the historical sound tokenization/integration defect;
the post-fix run below, not this failed run, describes later retrieval behavior.

Evaluation
[`eval-J6s-2026-09-01T19:30:07`](runs/eval-J6s-2026-09-01T19-30-07.json)
asked the same Codex model to locate
one 0–6 second rain, wind, and engine event with and without VidXP. Both runs
passed the harness contract.

| Condition | Predicted interval | IoU | End error | Time | Total / uncached input / output tokens | Tool activity | Promptfoo cost |
| --- | --- | ---: | ---: | ---: | --- | --- | ---: |
| VidXP-on | 0–8.0075 s | 0.7493 | +2.0075 s | 74.552 s | 301,712 / 48,423 / 1,769 | one skill load; six VidXP MCP calls; one non-media shell call | $0.815355 |
| VidXP-off | 0–6.8 s | 0.8824 | +0.8 s | 112.209 s | 329,961 / 35,906 / 3,623 | ten shell media-inspection calls | $0.812527 |

The VidXP run used fewer total tokens and finished faster, but Promptfoo's cost
was slightly higher because it used more uncached input. Cached input, uncached
input, and output use different rates, so total tokens alone do not determine
that estimate. Reasoning tokens are included in output tokens. Treat the dollar
value only as a within-run comparison metric, not an API invoice or measured
Codex-plan charge.

The saved post-FineLAP-fix job confirms that retrieval found the correct
opening region:

| Stage | Highest-ranked evidence |
| --- | --- |
| Action | 0–8.0075 s, rank 1 |
| Scene | 1.001–2.002 s, 2.002–3.003 s, and 3.003–4.004 s, ranks 1–3 |
| Sound | 1.76–1.92 s, 1.92–2.08 s, and 2.08–2.24 s, ranks 1–3 |
| Fused | 0–8.0075 s, rank 1 |

The ranking failure seen in an earlier run came from the FineLAP tokenization
bug fixed by commit `343bd27`; it is not evidence about the current system. In
the saved post-fix run, the fixed eight-second action record overlaps the finer
scene and sound hits. The then-current connected-component union therefore
adopted the action record's full end time. This explains the +2.0075-second
error in that run; production fusion now uses rank-anchored direct overlap.

The saved request used `top_k = 3`; at that revision, the application passed the
same value to each modality as retrieval depth and final output depth. That was
a separate candidate-depth limitation: a later boundary stage could not use
lower-ranked fine-grained evidence that was never retrieved. It does not by
itself explain the eight-second endpoint in this example. The retained scene
hits end at 4.004 seconds and the retained sound hits end at 2.24 seconds, so
those sparse boundaries also cannot determine the annotated 6-second end.
Paper-derived score-curve localization must be evaluated from the dense
sequence, not reconstructed from these seven retained hits.

The full-modality probe for this task queried all 572 indexed records with one
local text-embedding call per modality. It did not invoke Codex or rerun the
Promptfoo evaluation:

| Modality | Records | Highest-ranked interval | Best individual-record oracle |
| --- | ---: | --- | --- |
| Action | 10 | 0–8.0075 s, IoU 0.7493 | 0–8.0075 s, rank 1, IoU 0.7493 |
| Scene | 76 | 1.001–2.002 s, IoU 0.1668 | 2.002–3.003 s, rank 2, IoU 0.1668 |
| Sound | 486 | 1.76–1.92 s, IoU 0.0267 | 0–10 s, rank 43, IoU 0.6000 |

Individual dense records are intentionally short, so their oracle IoU is not a
boundary prediction. Their full timelines provide the useful evidence. Scene
records remain near the top through 7.007 seconds before their scores fall;
the FineLAP activation scores have a much larger within-modality drop between
seconds 6 and 7. The opening ten-second FineLAP global record ranks 43, while
the other global windows rank 480–486. Thus action, scene, and sound all rank
the correct opening region. That run's `top_k = 3` truncated the dense tail, and
interval union then let the coarse action record set the 8.0075-second endpoint.

This one task supports a transition near seven seconds, not an exact six-second
boundary. The remaining roughly one-second difference may come from the
one-second scene sampling grid, activation timing, or annotation convention;
it must be measured across the prepared tasks rather than corrected against
this annotation.

The original saved-ranking depth control replayed all ten collective tasks
through the then-production connected-component fusion without model or API
calls:

| Candidates per modality | Mean top-1 IoU | R@1 at .3/.5/.7 | Board R@3 at .3/.5/.7 | Output R@10 at .3/.5/.7 |
| ---: | ---: | --- | --- | --- |
| 3 | 0.1613 | .20/.20/.10 | .30/.30/.20 | .30/.30/.20 |
| 10 | 0.1718 | .20/.20/.20 | .40/.30/.30 | .40/.40/.30 |
| 20 | 0.1699 | .20/.20/.20 | .40/.40/.40 | .50/.40/.40 |
| 100 | 0.0434 | 0/0/0 | .10/.10/0 | .20/.10/0 |
| All | 0.0530 | 0/0/0 | 0/0/0 | 0/0/0 |

More candidates initially expose useful evidence but do not improve top-one
selection. At greater depth, adjacent records form transitive overlap chains;
the full-list top result for every task spans nearly the whole video. This
rejects both the shared input/output depth and a larger fixed replacement.
RRF can remain a ranking control only after the raw records have been converted
to bounded event proposals.

The production correction replaces transitive components with rank-anchored
direct overlap. One hit seeds each candidate, at most one hit from each other
modality can support it, and every supporting hit must overlap the seed itself.
The same saved rankings then produced:

| Candidates per modality | R@1 at .3/.5/.7 | R@3 at .3/.5/.7 | R@5 at .3/.5/.7 | R@10 at .3/.5/.7 |
| ---: | --- | --- | --- | --- |
| 3 | .10/.10/.10 | .30/.10/.10 | .30/.10/.10 | .30/.10/.10 |
| 20 | .10/.10/.10 | .20/.10/.10 | .20/.20/.10 | .40/.20/.10 |
| 100 | .10/.10/.10 | .30/.10/.10 | .30/.20/.10 | .30/.20/.10 |
| All | .10/.10/.10 | .30/.10/.10 | .30/.20/.10 | .30/.20/.10 |

Additional candidates no longer create video-length results. R@5 at tIoU 0.5
is still only `.20`: the correction preserves separate candidates but does not
repair coarse source windows or provider rankings. Product `top_k` now limits
only this final ranked list. A separate `candidate_top_k` defaults to 100
because 100 matched exhaustive input here; that is a bounded serving decision,
not a paper-derived or universally optimal depth.

The benchmark-only Point-to-Span ASG adaptation was then applied to the saved
curves without another model call:

| Method | Top interval | IoU | Start error | End error | Generated spans |
| --- | --- | ---: | ---: | ---: | --- |
| Previous union | 0–8.0075 s | 0.7493 | 0 s | +2.0075 s | Existing top-three hits |
| P2S ASG adaptation | 0.64–6.72 s | 0.7976 | +0.64 s | +0.72 s | Sound: 1; scene/action: 0 |

This is a concluded diagnostic, not an adopted product fix. It shows that the
published adaptive expansion can use FineLAP's dense curve, but the published
prominence threshold produced no scene or action span and the result remained
below the direct-inspection baseline's `0.8824` IoU. A full agent batch would
not resolve the remaining representation failure. It motivated the
overlapping-window control recorded next; that control is also concluded.

The frozen overlapping-window control then reindexed the development video at
4 samples per second, retaining VideoPrism's 16-frame input and advancing by 8
samples. This produces nominal four-second windows every two seconds:

| Method | Action rank 1 | Fused interval | Fused IoU | Action records |
| --- | --- | --- | ---: | ---: |
| Current eight-second records | 0–8.0075 s | 0–8.0075 s | 0.7493 | 10 |
| Four-second, two-second-stride records | 0–4.0204 s | 0–8.0244 s | 0.7477 | 38 |

The alternative's first three action hits were `0–4.0204`, `2.002–6.0224`,
and `4.004–8.0244` seconds. The second hit closely expressed the annotated
`0–6`-second endpoint, but connected-component fusion joined all three and
returned the wider interval. This experiment replaced the normal action index;
it did not retain eight-second records as a first stage or rerank the shorter
records inside them. The run took 165.094 seconds to index 38 VideoPrism
batches, used 11,929,970 index bytes, and took 0.512 seconds plus one
text-embedding call to query. Point-to-Span ASG produced no action candidate on
this curve because its strongest score is the first sample and
`scipy.signal.find_peaks` does not treat an endpoint as a peak.

This rejects only shorter overlapping records fed unchanged into the current
union. It does not reject the finer representation: selecting or reranking its
records without transitive union remained unevaluated at this stage.

The subsequent local comparison covered all five frozen held-out tasks that
declare action evidence. It compared the current eight-second records, the
four-second records ranked over the whole video, and a two-stage path that
kept fine records whose midpoint fell inside a top-three coarse record. The
two-stage path returned one fine record without interval union.

| Method | Mean top-1 IoU | R@1 at 0.5 | Top-3 candidate recall at 0.5 | Full-list candidate recall at 0.5 |
| --- | ---: | ---: | ---: | ---: |
| Current eight-second records | 0.0680 | 0.00 | 0.00 | 0.20 |
| Four-second records, whole video | 0.1297 | 0.20 | 0.40 | 0.60 |
| Top-three coarse records, then four-second records | 0.1297 | 0.20 | 0.40 | 0.40 |

Fine windows therefore improved the available candidates without producing a
reliable top result. Car-siren had a qualifying fine record at rank 13, but the
coarse top three missed its region. Engine-rev's near-target record ranked 48.
Sketch had a qualifying record at rank 3, while stir-and-cover succeeded at
rank 1 with IoU `0.6484`. A four-second record cannot represent the 15-second
signing reference; its best possible IoU was `0.2666`.

The fine indexes contained 307 records instead of 79 across three videos. The
first run measured 1,176.264 seconds of indexing; their durable generation
manifests record 1,175.579 seconds of build time and 5,966,316 committed bytes.
The shared profile store was 133,068,596 bytes including the earlier development
video. The comparison made five new local text-embedding calls and no Codex or
API calls. It rejects the tested coarse-top-three gate as a product rule. It
does not reject overlapping records as candidate evidence; their remaining
failure is ranking and variable-duration selection.

The next development control used the no-postprocessing ShotDetect path from
Diwan et al. PySceneDetect found three disjoint proposals. Existing SigLIP2
scores ranked the first proposal highest. The top sound hit overlapped only
that proposal; the top action hit overlapped it and the next proposal:

| Method | Top interval | IoU | End error | Evidence ranks |
| --- | --- | ---: | ---: | --- |
| Previous connected union | 0–8.0075 s | 0.7493 | +2.0075 s | Action 1, scene 1, sound 1 |
| Direct-inspection agent | 0–6.8 s | 0.8824 | +0.8 s | Agent media inspection |
| Shot proposal, scene score | 0–6.7401 s | 0.8902 | +0.7401 s | Scene 1 |
| Fixed shot, VidXP RRF score | 0–6.7401 s | 0.8902 | +0.7401 s | Action 1, scene 1, sound 1 |

Detection took about `1.7` seconds, produced three proposals, reused 76 scene
records, and made no model calls or index writes. This isolates the development
failure: retrieval ranks the correct region, but connected interval union
replaces its useful endpoint with the coarse action endpoint. The result does
not yet justify a product change because a single detected shot cannot show how
the rule behaves when a relevant moment crosses multiple shots.

The confirmed held-out comparison then evaluated tasks 3–10 without Codex.
Six tasks had scene scores for a direct scene-versus-RRF comparison; the two
action-and-sound tasks were reported separately rather than given undeclared
scene evidence.

| Method and scope | Tasks | Mean IoU | Rate at tIoU 0.3 / 0.5 / 0.7 | Mean absolute start / end error |
| --- | ---: | ---: | --- | --- |
| Previous connected union, all | 8 | 0.0418 | 0 / 0 / 0 | 59.06 / 59.05 s |
| Fixed shot with RRF, all | 8 | 0.0882 | 0.125 / 0 / 0 | 93.45 / 59.59 s |
| Best single-shot oracle, all | 8 | 0.5219 | 0.625 / 0.375 / 0.375 | 18.34 / 8.70 s |
| Scene-ranked shot, scene tasks | 6 | 0.2841 | 0.333 / 0.167 / 0.167 | 54.29 / 39.78 s |
| Fixed shot with RRF, same scene tasks | 6 | 0.1175 | 0.167 / 0 / 0 | 84.38 / 37.73 s |

For selected outputs, the threshold rate is R@1. For the best-shot oracle, it
is candidate recall: whether any single detected shot reaches the threshold.

RRF helped none of the six comparable tasks. It retained three scene winners,
changed two zero-IoU winners to different zero-IoU winners, and harmed one. On
`phone-ring`, the scene-ranked proposal matched the reference at IoU `0.9995`.
RRF instead selected a wrong proposal with scene rank 2 and sound rank 3,
producing IoU `0.0`; its two rank contributions outweighed the correct
proposal's scene rank 1. Both action-and-sound tasks remained at IoU `0.0`.

The proposal oracle separates the remaining failures. Five tasks cannot reach
tIoU `0.5` with any single detected shot; three can, but ranking misses the
candidate. Four of eight RRF winners use evidence that overlaps more than one
proposal. None of the references crosses a detected boundary after a
`0.05`-second tolerance, so this slice does not test multi-shot merging.

Preparing the saved curves took `46.744` seconds and 16 local text-embedding
calls. Detecting shots across four unique videos took about `17` seconds, with no
model calls or index writes. Peak memory was not measured. This is a local
component comparison, not an agent or Promptfoo pilot run.

### Query wording and FineLAP stream control

A second local control compared the unchanged full query with manually separated
modality phrases on the same eight held-out tasks. The phrases used only content
stated in each task query; they did not use timestamps, retrieved results, or
video inspection. This is a wording ceiling, not an automatic planner result.

| Ranking check over 16 task-modality pairs | Full query | Separated phrase |
| --- | ---: | ---: |
| Target-overlapping evidence in top 3 | 7 | 8 |
| Best-boundary record in top 3 | 4 | 7 |

Nine target-overlap ranks improved, five were unchanged, and two worsened. The
mixed result rejects query rewriting as the immediate product fix. For example,
the phone-ring sound rank improved from 22 to 1, while the stir-and-cover scene
rank fell from 1 to 41.

FineLAP uses separate audio projectors for whole-clip retrieval and frame-level
event localization. At the time of this diagnostic, VidXP stored both outputs
in one sound collection and ranked them together. Filtering the existing index
into those published paths changed sound candidate recall:

| Sound task | Current mixed rank | 10-second window rank | Dense activation rank |
| --- | ---: | ---: | ---: |
| Car siren | 431 | 2 | 177 |
| Engine rev | 147 | 3 | 141 |
| Phone ring | 22 | 8 | 1 |
| Drumbeat | 1,020 | 13 | 829 |

The target entered a top-three list on three of four tasks instead of zero of
four. A short sound phrase produced the same three-task coverage when used for
both streams. This supports keeping FineLAP's clip and frame rankings separate;
it does not define how to turn both lists into one final interval. The control
made 32 local text-embedding calls in about `14` seconds, with no Codex/API
calls or index writes.

The first 2026-09-03 correction made standard sound search return only global
clips. Evaluation `eval-mw5-2026-09-02T19:40:44` then returned `0–10` seconds
for VidXP and `0–6.81` seconds for direct inspection, against a `0–6` reference.
VidXP identified the same event, finished 11.3% faster, used 16.5% fewer total
tokens and two fewer tool calls, and had a provider estimate of `$0.401271`
versus `$0.968155`. Its IoU nevertheless fell to `0.6000` because the agent
returned the ten-second sound envelope.

That result rejects global-only sound output as the complete product behavior.
The replacement used three global clips as a gate, then pooled and ranked their
dense activations. On the four held-out sound tasks, the gate covered two targets
but the final top three covered none; the two surviving target activations ranked
`132` and `63`. Final sound-only mean IoU and R@1 at tIoU 0.3/0.5/0.7 were all
zero against the one accepted interval per task.

A later source-audio audit invalidated using those four numbers as a provider
quality estimate. The phone interval is effectively silent, while the engine
phrase has several correct acoustic occurrences; for example, a later model's
top frame at 242.22 seconds lies inside LongVALE's separate
241.760–243.554-second engine-rev annotation. The 25.560–27.560-second reference
is distinguished by a visual clause about the driver gesturing. The exact
target-only result above remains reproducible, but it neither accepts nor
rejects the sound provider. It is an auxiliary component diagnostic and does
not block the collective paired run.

## Runtime and model generations

The legacy and current checks used the same physical laptop, as confirmed for
this rerun:

- HP ENVY Laptop 16-h0xxx;
- Intel Core i7-12700H, 14 cores and 20 logical processors;
- 15.72 GiB system memory;
- NVIDIA GeForce RTX 3060 Laptop GPU with 4 GiB VRAM present but unused;
- CPU-only PyTorch execution.

| Generation | Speech embedding | Scene embedding | Sampling/window | Transcription in these benchmarks |
|---|---|---|---|---|
| Legacy full, 2026-07-27 | `all-MiniLM-L6-v2` | OpenAI CLIP `ViT-B/32` through `clip-anytorch` | HiREST 0.8-duration window; DiDeMo fixed 30-frame stride and max chunk pooling | Released HiREST SRTs; WhisperX `large-v2` was not exercised |
| Current smoke, 2026-07-30 | `Qwen/Qwen3-Embedding-0.6B` at `97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3` | `google/siglip2-base-patch16-224` at `75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2` | HiREST same 0.8-duration window; DiDeMo source-aware 1.0 sample/sec and max chunk pooling | Released HiREST SRTs; faster-whisper `large-v3-turbo` was not exercised |

The surviving legacy run record did not pin immutable model revisions or a
complete package/OS snapshot. The current smoke manifests record Windows 11,
Python 3.14.0, PyTorch 2.13.0+cpu, Transformers 5.14.1, and
Sentence Transformers 5.6.1. This limitation is stated instead of inventing
legacy revision metadata after the fact.

## Multimodal comparison contract

Natural-language answer prose is not an official benchmark prediction format.
Benchmark runs continue to preserve atomic scene and speech hits, raw
distances, and the existing dataset serializers. When a dataset contains both
eligible modalities, reports must show three fixed rows:

| Retrieval path | What is compared |
|---|---|
| Scene only | The existing visual retrieval output |
| Speech only | The existing transcript retrieval output |
| Fixed RRF fusion | Rank-anchored, directly overlapping candidates ranked with `rrf_v2`, `k=60` |

No fused benchmark score is reported until the same frozen dataset inputs and
evaluator used by the atomic rows have been run. Generated `QueryAnswer` claims
remain a separate grounding evaluation and cannot replace these retrieval
comparisons.

## What the measurements mean

### Rank@1 and Rank@5

DiDeMo gives VidXP 21 possible time ranges for every search.

- **Rank@1** is how often VidXP's first choice is accepted as correct.
- **Rank@5** is how often an accepted answer appears anywhere in VidXP's first
  five choices.

These values measure result ordering. They are not VidXP confidence scores.

### Time-range overlap

Intersection over Union, normally shortened to **IoU**, measures how closely a
predicted time range overlaps the correct range.

For example, suppose the correct range is 10–20 seconds and VidXP predicts
12–22 seconds. The ranges overlap for 8 seconds and together cover 12 seconds:

```text
overlap = 8 / 12 = 0.67
```

- **Mean IoU** is the average overlap across all searches.
- **R@0.5** is the percentage of searches with overlap of at least 0.5.
- **R@0.7** is the percentage with overlap of at least 0.7, so it requires a
  more precise prediction.

## DiDeMo

### Legacy full-result comparison

| Method | Rank@1 | Rank@5 | Mean IoU |
|---|---:|---:|---:|
| Chance | 3.75% | 22.50% | 22.64% |
| Common-position guess | 19.40% | 66.38% | 26.65% |
| **VidXP legacy CLIP baseline** | **20.19%** | **55.71%** | **34.60%** |
| Published MCN trained on DiDeMo | 28.10% | 78.21% | 41.08% |

The common-position guess ignores the search text and video content. It ranks
time ranges according to which positions are often correct in the dataset.

VidXP places its first result closer to the correct time than that basic guess,
but the rest of its first five choices are ordered less effectively. The
DiDeMo-trained MCN system remains better on all three measurements.

### Honest conclusion

The legacy VidXP stack produces meaningful visual localization, but its candidate ranking is not
yet competitive with the published trained system. Improving how scene evidence
is combined across nearby frames is the clearest DiDeMo improvement target.

The run processed every official test query. One corrupt official media object
was replaced with the archived original of the same source video. The replacement
and its checksum are recorded in the
[technical run record](adapter_validation.md#full-didemo-test-result).

## HiREST

### Legacy full-result comparison

| Method on the same 193 validation pairs | R@0.5 | R@0.7 |
|---|---:|---:|
| Return almost the entire video without using the query | 68.91% | 23.83% |
| **VidXP legacy MiniLM transcript search** | **78.24%** | **44.56%** |
| Improvement | **+9.33 points** | **+20.73 points** |

VidXP turns the transcript matches into a score over the video's timeline and
selects the highest-scoring continuous window covering 80% of the video. That
broad window size was selected using the same validation set shown above.
HiREST's correct moments are often long, which is why even the query-free
comparison scores highly.

### Honest conclusion

The transcript match adds useful timing information, particularly under the
stricter 0.7 overlap requirement. This does not yet prove precise localization
or superiority over published HiREST systems because:

- the 80% window was selected on the reported validation data;
- the run used transcripts released by HiREST, so it did not test WhisperX;
- the released test answers contain placeholder time ranges and cannot be
  scored locally.

The result is a useful legacy validation baseline, not a final held-out paper
result. The current two-video Qwen3 smoke establishes compatibility only; it
does not supersede this score.

## Next approved comparison

After explicit maintainer approval, run the paired Codex comparison against the
current collective system. Report the answer and evidence, the atomic modality
hits that formed each fused result, IoU and boundary errors, every token
category, elapsed time, estimated cost, and tool calls. The sound-only control
remains a separate diagnosis; neither its failure nor a passing replacement
would itself be a LongVALE system result.

## Sources and reproduction

- [Published competitor tables](published_results.md)
- [DiDeMo and HiREST run details](adapter_validation.md)
- [Benchmark selection and limitations](benchmark_catalog.md)
- [Runtime checks](runtime_validation.md)
