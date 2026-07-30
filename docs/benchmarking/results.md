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

The current-provider rows are deliberately tiny regression runs. Their
percentages are not quality estimates and must not be compared with the full
legacy rows. A current full-corpus score has not been run.

## Runtime and model generations

The legacy and current checks used the same physical laptop, as confirmed for
this rerun:

- HP ENVY Laptop 16-h0xxx;
- Intel Core i7-12700H, 14 cores and 20 logical processors;
- 15.72 GiB system memory;
- NVIDIA GeForce RTX 3060 Laptop GPU with 4 GiB VRAM present but unused;
- CPU-only PyTorch execution.

| Generation | Dialogue embedding | Scene embedding | Sampling/window | Transcription in these benchmarks |
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
Benchmark runs continue to preserve atomic scene and dialogue hits, raw
distances, and the existing dataset serializers. When a dataset contains both
eligible modalities, reports must show three fixed rows:

| Retrieval path | What is compared |
|---|---|
| Scene only | The existing visual retrieval output |
| Dialogue only | The existing transcript retrieval output |
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

## Next benchmark: LongVALE

LongVALE is the next useful experiment because it combines visual and spoken
evidence in longer videos. The immediate work is limited to:

1. Convert LongVALE event descriptions into VidXP visual and dialogue searches.
2. Combine those two result lists using one fixed rule.
3. Return the single start/end range required by the official evaluator.
4. Process one of the nine evaluation archives to measure runtime, temporary
   storage, and index growth.
5. Run the complete evaluation only if that pilot finishes cleanly.

VidXP does not currently understand general sound events. Sound-only misses must
remain in the official result and be disclosed rather than filtered out.

## Sources and reproduction

- [Published competitor tables](published_results.md)
- [DiDeMo and HiREST run details](adapter_validation.md)
- [Benchmark selection and limitations](benchmark_catalog.md)
- [Runtime checks](runtime_validation.md)
