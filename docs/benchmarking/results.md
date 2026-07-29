# Current benchmark results

This page answers three questions:

1. What did VidXP score?
2. What do the measurements mean?
3. What can we honestly conclude from them?

Detailed artifacts, hashes, commands, and evaluator behavior remain in the
[adapter validation ledger](adapter_validation.md).

## Results at a glance

| Benchmark | Evaluated data | VidXP result | Status |
|---|---|---|---|
| DiDeMo | Official test: 4,021 searches over 1,037 videos | Rank@1 **20.19%**, Rank@5 **55.71%**, average overlap **34.60%** | Valid official test baseline with one documented replacement for a corrupt copy of an official video |
| HiREST | Official validation: 193 known-video searches | R@0.5 **78.24%**, R@0.7 **44.56%** | Validation result; the prediction-window setting was selected on this same validation set |
| HiREST | Released test: 776 known-video searches | Predictions generated, no score | Public test boundaries are placeholders, so local scoring would be meaningless |

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

### Comparison

| Method | Rank@1 | Rank@5 | Mean IoU |
|---|---:|---:|---:|
| Chance | 3.75% | 22.50% | 22.64% |
| Common-position guess | 19.40% | 66.38% | 26.65% |
| **VidXP** | **20.19%** | **55.71%** | **34.60%** |
| Published MCN trained on DiDeMo | 28.10% | 78.21% | 41.08% |

The common-position guess ignores the search text and video content. It ranks
time ranges according to which positions are often correct in the dataset.

VidXP places its first result closer to the correct time than that basic guess,
but the rest of its first five choices are ordered less effectively. The
DiDeMo-trained MCN system remains better on all three measurements.

### Honest conclusion

VidXP produces meaningful visual localization, but its candidate ranking is not
yet competitive with the published trained system. Improving how scene evidence
is combined across nearby frames is the clearest DiDeMo improvement target.

The run processed every official test query. One corrupt official media object
was replaced with the archived original of the same source video. The replacement
and its checksum are recorded in the
[technical run record](adapter_validation.md#full-didemo-test-result).

## HiREST

### Comparison

| Method on the same 193 validation pairs | R@0.5 | R@0.7 |
|---|---:|---:|
| Return almost the entire video without using the query | 68.91% | 23.83% |
| **VidXP transcript search** | **78.24%** | **44.56%** |
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

The result is a useful validation baseline, not a final held-out paper result.

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
