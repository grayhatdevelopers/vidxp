# Benchmark results

This page answers three questions:

1. What did VidXP score?
2. What do the measurements mean?
3. What can we honestly conclude from them?

Detailed artifacts, hashes, commands, and evaluator behavior remain in the
[adapter validation ledger](adapter_validation.md).

## Evidence at a glance

| Generation | Benchmark | Evaluated data | Result | What it supports |
|---|---|---|---|---|
| Legacy full | DiDeMo | Official test: 4,021 searches over 1,037 videos | Rank@1 **20.19%**, Rank@5 **55.71%**, mean IoU **34.60%** | Full visual baseline with one documented replacement for a corrupt official media object |
| Legacy full | HiREST | Official validation: 193 known-video searches | R@0.5 **78.24%**, R@0.7 **44.56%** | Validation baseline; the 0.8 prediction-window fraction was selected on this same validation set |
| Legacy full | HiREST | Released test: 776 known-video searches | Predictions generated, not scored | Public test boundaries are placeholders, so local scoring would be meaningless |
| Current smoke | DiDeMo | Official test annotation index `0`; one video | Rank@1 **0**, Rank@5 **1**, mean IoU **0** | Real SigLIP2 execution, serialization, and official-evaluator check only |
| Current smoke | HiREST | Two declared validation pairs over two videos | R@0.5 **50**, R@0.7 **50** | Real Qwen3 execution, multi-video storage, filtered search, serialization, and official-evaluator check only |
| Agent development smoke | Codex MCP ablation | LongVALE-derived task `ZYT-rain-wind-engine`; one paired run | VidXP-on IoU **0.7493**; VidXP-off IoU **0.8824** | Harness, skill/MCP isolation, deterministic scoring, and reporting check only; not a held-out pilot or LongVALE result |

The current-provider rows are deliberately tiny regression runs. Their
percentages are not quality estimates and must not be compared with the full
legacy rows. A current full-corpus score has not been run.

## Codex MCP development smoke

Evaluation `eval-J6s-2026-09-01T19:30:07` asked the same Codex model to locate
one 0–6 second rain, wind, and engine event with and without VidXP. Both runs
passed the harness contract.

| Condition | Predicted interval | IoU | End error | Time | Total / uncached input / output tokens | Tool activity | Estimated cost |
| --- | --- | ---: | ---: | ---: | --- | --- | ---: |
| VidXP-on | 0–8.0075 s | 0.7493 | +2.0075 s | 74.552 s | 301,712 / 48,423 / 1,769 | one skill load; six VidXP MCP calls; one non-media shell call | $0.815355 |
| VidXP-off | 0–6.8 s | 0.8824 | +0.8 s | 112.209 s | 329,961 / 35,906 / 3,623 | ten shell media-inspection calls | $0.812527 |

The VidXP run used fewer total tokens and finished faster, but its provider-
estimated cost was slightly higher because it used more uncached input. Cached
and uncached input can have different rates; total tokens alone do not determine
cost. Reasoning tokens are included in output tokens. Subscription-authenticated
Codex usage is an account allowance or credit measurement, not an API invoice.

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
the current run, the fixed eight-second action record overlaps the finer scene
and sound hits. Connected-component union therefore adopts the action record's
full end time. This explains the +2.0075-second error.

The request also used `top_k = 3`, which the current application passes to each
modality as both retrieval depth and final output depth. That is a separate
candidate-depth limitation: a later boundary stage cannot use lower-ranked
fine-grained evidence that was never retrieved. It does not by itself explain
the eight-second endpoint in this example. The retained scene hits end at
4.004 seconds and the retained sound hits end at 2.24 seconds, so those sparse
boundaries also cannot determine the annotated 6-second end. Paper-derived
score-curve localization must be evaluated from the dense sequence, not
reconstructed from these seven retained hits.

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
the correct opening region. The current `top_k = 3` truncates the dense tail,
and interval union then lets the coarse action record set the 8.0075-second
endpoint.

This one task supports a transition near seven seconds, not an exact six-second
boundary. The remaining roughly one-second difference may come from the
one-second scene sampling grid, activation timing, or annotation convention;
it must be measured across the prepared tasks rather than corrected against
this annotation.

The benchmark-only Point-to-Span ASG adaptation was then applied to the saved
curves without another model call:

| Method | Top interval | IoU | Start error | End error | Generated spans |
| --- | --- | ---: | ---: | ---: | --- |
| Current union | 0–8.0075 s | 0.7493 | 0 s | +2.0075 s | Existing top-three hits |
| P2S ASG adaptation | 0.64–6.72 s | 0.7976 | +0.64 s | +0.72 s | Sound: 1; scene/action: 0 |

This is a concluded diagnostic, not an adopted product fix. It shows that the
published adaptive expansion can use FineLAP's dense curve, but the published
prominence threshold produced no scene or action span and the result remained
below the direct-inspection baseline's `0.8824` IoU. A full agent batch would
not resolve the remaining representation failure. The next comparison must
first test temporal units that can represent shorter boundaries.

The frozen overlapping-window control then reindexed the development video at
4 samples per second, retaining VideoPrism's 16-frame input and advancing by 8
samples. This produces nominal four-second windows every two seconds:

| Method | Action rank 1 | Fused interval | Fused IoU | Action records |
| --- | --- | --- | ---: | ---: |
| Current eight-second records | 0–8.0075 s | 0–8.0075 s | 0.7493 | 10 |
| Four-second, two-second-stride records | 0–4.0204 s | 0–8.0244 s | 0.7477 | 38 |

The alternative's first three action hits were `0–4.0204`, `2.002–6.0224`,
and `4.004–8.0244` seconds. Connected-component fusion joined all three, so a
representation capable of expressing the target boundary still returned a
wider interval. The run took 165.094 seconds to index 38 VideoPrism batches,
used 11,929,970 index bytes, and took 0.512 seconds plus one text-embedding call
to query. Point-to-Span ASG produced no action candidate on this curve because
its strongest score is the first sample and `scipy.signal.find_peaks` does not
treat an endpoint as a peak.

This rejects shorter overlapping records as a sufficient fix by themselves.
It also confirms the next layer: proposal selection or boundary inference must
avoid transitive union of adjacent same-modality windows. Do not run this
profile across the held-out agent tasks.

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
| Fixed RRF fusion | Overlap-connected intervals ranked with `rrf_v1`, `k=60` |

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

## Next combined benchmark

The FineLAP environmental-sound layer is implemented but has no VidXP quality
result yet. LongVALE is the primary next experiment because it contains visual,
generic-audio, and spoken evidence in long videos. The work is ordered as follows:

1. Complete a bounded real-media FineLAP integration smoke and record resource use.
2. Convert LongVALE event descriptions into visual, sound, and speech searches.
3. Combine those result lists using one fixed, provenance-preserving rule.
4. Return the single start/end range required by the official evaluator.
5. Process one of the nine evaluation archives to measure runtime, temporary
   storage, and index growth.
6. Run the complete evaluation only if that pilot finishes cleanly.

VidXP now indexes general sound events, but implementation is not evidence of
retrieval or boundary quality. The full LongVALE query set must remain in the
official denominator, including sound-only misses. See
[multimodal model direction](model_selection.md) for the selection evidence and
benchmark roles.

## Sources and reproduction

- [Published competitor tables](published_results.md)
- [DiDeMo and HiREST run details](adapter_validation.md)
- [Benchmark selection and limitations](benchmark_catalog.md)
- [Runtime checks](runtime_validation.md)
