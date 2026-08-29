# Official adapter validation ledger

This ledger records the implementation and executable validation of the first
two benchmark adapters. Subset runs are smoke tests of the complete data,
indexing, serialization, and evaluator path. Their metrics are not paper scores.

For the recorded results, metric definitions, and plain-language interpretation,
start with [current benchmark results](results.md). This ledger is the technical
reproduction record.

The full-corpus scores below are the retained 2026-07-27 legacy-provider
results. The 2026-07-30 SigLIP2/Qwen3 runs are bounded execution smokes, not
replacement quality scores. Both generations used the same physical laptop;
the model and protocol differences are recorded explicitly.

## Pinned official artifacts

| Benchmark | Artifact | Revision | SHA-256 |
|---|---|---|---|
| DiDeMo | [`data/val_data.json`](https://github.com/LisaAnne/LocalizingMoments/blob/b6a555c8134581305d0ed4716fbc192860e0b88c/data/val_data.json) | `b6a555c8134581305d0ed4716fbc192860e0b88c` | `b0364cc256553332feb19d46bcc4cd2b09774949fe6c0b25e7ed0ff3c6aefebb` |
| DiDeMo | [`data/test_data.json`](https://github.com/LisaAnne/LocalizingMoments/blob/b6a555c8134581305d0ed4716fbc192860e0b88c/data/test_data.json) | `b6a555c8134581305d0ed4716fbc192860e0b88c` | `1891c04ec48b3d364c739594b2b6413806b74bd9027c092d896e7ebb930ff1cd` |
| DiDeMo | [`utils/eval.py`](https://github.com/LisaAnne/LocalizingMoments/blob/b6a555c8134581305d0ed4716fbc192860e0b88c/utils/eval.py) | `b6a555c8134581305d0ed4716fbc192860e0b88c` | `9ec3e7a171272eb3551b0eaa7bbe9292131ad5cf34fd5c1e02c0fc4a11234df6` |
| DiDeMo | [`data/yfcc100m_hash.txt`](https://github.com/LisaAnne/LocalizingMoments/blob/b6a555c8134581305d0ed4716fbc192860e0b88c/data/yfcc100m_hash.txt) | `b6a555c8134581305d0ed4716fbc192860e0b88c` | `481d9aaf020624d5915200bcf4752fb46d3e1931167e8b46715a5f342577cc4d` |
| HiREST | [`data/splits/all_data_val.json`](https://github.com/j-min/HiREST/blob/deffc169b4e8d51c1589d5512ad05da61e81bcee/data/splits/all_data_val.json) | `deffc169b4e8d51c1589d5512ad05da61e81bcee` | `70d32c5fcdffe66cbf3c732dd274f03378da2082f50c9cec7e67705f529ecb4d` |
| HiREST | [`data/splits/all_data_test.json`](https://github.com/j-min/HiREST/blob/deffc169b4e8d51c1589d5512ad05da61e81bcee/data/splits/all_data_test.json) | `deffc169b4e8d51c1589d5512ad05da61e81bcee` | `00219050c022ff2fc89c210ca4db605de6aa13c5c6014e4c678345ade3448a62` |
| HiREST | [`data/evaluation/categories.json`](https://github.com/j-min/HiREST/blob/deffc169b4e8d51c1589d5512ad05da61e81bcee/data/evaluation/categories.json) | `deffc169b4e8d51c1589d5512ad05da61e81bcee` | `157623d50f7b8482f55fa1c4efc500539784c0399fb2dd60bb687b4006d85ca1` |
| HiREST | [`evaluate.py`](https://github.com/j-min/HiREST/blob/deffc169b4e8d51c1589d5512ad05da61e81bcee/evaluate.py) | `deffc169b4e8d51c1589d5512ad05da61e81bcee` | `871b48dc5ce42fbe1a4b672fe4df88a88ce568d57759dfc971e5aacc5f88f119` |
| HiREST | [`ASR.zip`](https://huggingface.co/j-min/HiREST-baseline/resolve/54e2f8da7a4384fec8a137011399f5e104069032/ASR.zip) | `54e2f8da7a4384fec8a137011399f5e104069032` | `0b452d38e30064dc7273a58b7b73ec33e307ff83d30048a472777f56e3a29fbc` |

The table records canonical raw upstream bytes. The adapters also accept the
semantically identical CRLF evaluator hashes produced by a Windows Git
checkout: `4754bb320564e5d2e7c633e0b660e87feca7f00fa73269e50140e81ffb4ca762`
for DiDeMo and
`c4b8ba9b572ae4088e90ddc3eec2b2cc4f5b4c1a0153ff6e0843817da89a5ca0`
for HiREST. The observed hash is recorded rather than silently normalizing a
user-supplied checkout.

The adapters verify these hashes before indexing. Each run manifest also records
the artifact paths, URLs, revisions, sizes, and observed hashes. VidXP does not
replace the official downloaders; the adapters consume prepared media and ASR.

## Validated official split facts

The pinned DiDeMo test split contains 4,021 annotations over 1,037 videos. Of
these, 473 annotations over 122 videos declare `num_segments: 5`; their labels do
not use chunk 5.

The pinned DiDeMo validation split contains 4,180 annotations over 1,094
videos. The pinned HiREST validation split contains 292 prompts and 193
`clip: true` moment pairs over 193 videos.

The pinned HiREST test split contains 546 prompts. Prediction generation covers
exactly 776 `clip: true` prompt/video pairs across 382 prompts and 776 unique
videos. Every one of those videos has a matching SRT in the pinned released-ASR
archive. Entries with `clip: false` do not enter the moment-retrieval adapter.
All 776 released test bounds are `[0, 1]` placeholders, and the official README
only demonstrates moment-retrieval evaluation on validation. The adapter
therefore retains validated test predictions as an unscored submission artifact
instead of reporting meaningless local test metrics.

## DiDeMo behavior

1. Verify and read the official annotation file.
2. Index each selected video through the scene-only core.
3. Search every selected annotation against all sampled frames of its known
   video.
4. Ignore frames at or after 30 seconds, retain the maximum sampled-frame score
   inside each of the six five-second chunks, and average the included chunk
   scores for each moment.
5. Rank the official candidate set: six single chunks followed by the 15
   `(start, end)` combinations defined in the official repository.
6. For five-segment videos, rank all 15 available moments before the six moments
   involving unavailable chunk 5. All 21 candidates remain in the file because
   the official evaluator requires 21 predictions per annotation.
7. Reject missing, duplicate, illegal, or misordered candidates before writing
   `predictions.json`.
8. Invoke the pinned evaluator.

The evaluator is Python 2 source. The compatibility runner loads that pinned
source and changes only its three `print` statements to Python 3 syntax. Its
rank and IoU expressions are not copied or altered. `evaluator.log` records the
command, working directory, compatibility note, output, and return code.

## HiREST released-ASR behavior

1. Verify the test split, categories, evaluator, and released-ASR archive.
2. Select only declared `clip: true` prompt/video pairs.
3. Parse the matching released SRT files with the `srt` package.
4. Split each SRT cue into the configured five-word speech phrases. Because
   released SRT cues do not contain word timestamps, phrase bounds are
   interpolated linearly within the real cue bounds and disclosed in the run
   manifest.
5. Submit the timestamped phrases to the speech-only VidXP core. The earlier
   full run used MiniLM; the current smoke used Qwen3 Embedding. Neither path
   loads a transcription model or decodes video.
6. Search each prompt only within its known video and retrieve every stored
   speech phrase. Project phrase scores onto one-second bins, assign uncovered
   seconds an explicit absence penalty, and rank duration-relative windows by
   their mean score with an earliest-start tie break.
7. Clamp the selected window to the official video duration, then reject missing,
   non-finite, zero-length, negative, or structurally invalid predictions.
8. Serialize the exact nested official form:

   ```json
   {
     "prompt": {
       "video.mp4": {
         "bounds": [12.0, 18.5]
       }
     }
   }
   ```

9. Invoke the unchanged official evaluator with
   `--task moment_retrieval`.

The evaluator imports `language_evaluation` at module load although moment
retrieval never references it. The adapter supplies an empty temporary import
shim for that unused captioning dependency. The official file and moment metric
logic remain unchanged, and the shim is disclosed in `evaluator.log`.

## Validation-frozen adapter choices

These choices were made only on official validation data. Test annotations were
not used to select either setting.

For the legacy DiDeMo run, a declared 15-annotation validation subset over four
downloadable official videos was indexed at frame stride `30`. Both
alternatives used the same stored CLIP frame scores and the pinned official
evaluator:

| Within-chunk pooling | Rank@1 | Rank@5 | mIoU |
|---|---:|---:|---:|
| mean | 0.133333 | 0.400000 | 0.188889 |
| max | 0.266667 | 0.466667 | 0.322222 |

Max pooling won all three validation measures and is therefore the default. In
the earlier failed test smoke, the strongest individual frame was at 22.022
seconds inside the human-selected chunk, but mean pooling diluted it enough to
rank chunk 3 first. This is why the change is an aggregation correction rather
than a label-specific test patch.

For the legacy HiREST run, all 193 official validation moment pairs were
indexed from released ASR. The released cues contain no word timestamps, so the
repaired core first created the configured five-word phrases using linear
interpolation inside each real cue. The same stored MiniLM scores were then
evaluated over this declared duration-fraction grid:

| Window fraction | R@0.5 | R@0.7 |
|---:|---:|---:|
| 0.25 | 4.6632 | 1.5544 |
| 0.40 | 31.6062 | 4.6632 |
| 0.50 | 47.1503 | 19.1710 |
| 0.60 | 60.6218 | 31.0881 |
| 0.70 | 72.0207 | 42.4870 |
| **0.80** | **78.2383** | **44.5596** |
| 0.85 | 76.1658 | 43.0052 |
| 0.90 | 73.5751 | 40.4145 |
| 0.95 | 72.5389 | 34.1969 |

The initial fixed-seconds study was rejected as the default because HiREST
validation moments have a median length of 127 seconds and a median
moment-to-video ratio of 0.6006. Its R@0.5/R@0.7 values rose from `0/0` for
one- to eight-second windows to `44.0415/16.5803` at 128 seconds and
`67.8756/29.0155` at 384 seconds; windows longer than some videos collapsed to
whole-video predictions. The duration-relative form avoids that structural
failure across differently sized videos.

As a metric sanity check, predicting every full video without using the query
scored R@0.5 `68.9119` and R@0.7 `23.8342` on the same validation pairs. The
selected 0.80 query-scored window beats that prior on both metrics, especially
the stricter threshold. The broad-window prior must still accompany any later
paper result because HiREST moments occupy a large part of their videos.

The clean final run `hirest-final-validation-20260727` rebuilt all 193 videos
under the final implementation fingerprint, completed 193/193 checkpoints with
an empty failure log, and reproduced R@0.5 `78.23834196891191` and R@0.7
`44.559585492227974` through the unchanged official evaluator. Its manifest
classifies the output as validation-only, not a paper score.

### Full DiDeMo test result

The run `didemo-full-test-final-20260727` indexed 1,037/1,037 test videos,
generated all 4,021 predictions with all 21 official candidates, and completed
with an empty failure log. The pinned evaluator returned:

| Rank@1 | Rank@5 | Mean IoU |
|---:|---:|---:|
| `0.20193981596617758` | `0.5570753543894553` | `0.3460485230318605` |

One media exception must accompany this result. The
[official Multimedia Commons object](https://multimedia-commons.s3-us-west-2.amazonaws.com/data/videos/mp4/deb/3d8/deb3d8c8aba7077b378d16b236b0a5.mp4)
for Flickr video `13482799053` is 151,552 bytes and FFmpeg/OpenCV reject its
contradictory MP4 sample tables. It was replaced with Wikimedia Commons'
[archived original of the same Flickr item](https://commons.wikimedia.org/wiki/File:Common_Starlings_flying_away_from_a_Marsh_Harrier.webm),
whose SHA-1 `2aefa90d4256e74cf62e492729c0e0f6d6bede72` and 94,107,862-byte
size match the archive record. The manifest therefore classifies this as an
official test result with a documented media substitution, not an untouched
official-media run.

## 2026-07-30 current-provider regression runs

These checks used the current CPU providers after the product model swap. They
were intentionally bounded after the integration path was proven; they are not
full benchmark replacements.

### Hardware and runtime

| Item | Recorded current setup |
|---|---|
| Laptop | HP ENVY Laptop 16-h0xxx; the same physical hardware used for the legacy runs |
| CPU | Intel Core i7-12700H; 14 cores, 20 logical processors |
| Memory | 15.72 GiB |
| GPU | NVIDIA GeForce RTX 3060 Laptop GPU, 4 GiB; present but unused |
| Runtime | Windows 11; Python 3.14.0; PyTorch 2.13.0+cpu |
| Libraries | Transformers 5.14.1; Sentence Transformers 5.6.1; ChromaDB 1.5.9 |

HiREST used `Qwen/Qwen3-Embedding-0.6B` at immutable revision
`97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3`. DiDeMo used
`google/siglip2-base-patch16-224` at
`75de2d55ec2d0b4efc50b3e9ad70dba96a7b2fa2`. HiREST consumed the released
SRTs, so the configured faster-whisper model was not loaded or evaluated.

### Real execution results

| Run | Declared subset | Result | Runtime evidence |
|---|---|---|---|
| `hirest-qwen3-generation-smoke-20260730` | Two validation prompt/video pairs, including `Make Oatmeal Pancake Mix` / `5V3dI2zp1xA.mp4` | Official evaluator: R@0.5 `50.0`, R@0.7 `50.0`; classification `validation_smoke_test_not_paper_score` | Both media items remained searchable in the run generation; 64.923 recorded stage seconds; 2,593,096-byte store; empty failure log |
| `didemo-siglip2-sample-20260730` | Official test annotation index `0`; one video; 1.0 sample/sec; max chunk pooling | Official evaluator: Rank@1 `0.0`, Rank@5 `1.0`, mIoU `0.0`; classification `smoke_test_not_paper_score` | 29.359 recorded stage seconds; 747,684-byte store; all 21 candidates serialized; empty failure log |

Prediction SHA-256 values were
`c2cee45eb7802e2c19adb4aec876a8fe2c203c59dde4bb20b41634b83d050afd`
for HiREST and
`4a38ff2c307833393f96fc0af48107a12581ecd9be734d8c0b906b2448a6fd33`
for DiDeMo. The runs were made from commit
`880da785825e95c46bfe050763be42afb02e844f` with the documented uncommitted
benchmark repairs, whose implementation fingerprint was
`afe0ee120f1c7b7daabe6d2c51e320a67a1368158fc5215acc34b7dd3e4e245e`.

### Defects exposed and fixed

The first current-provider attempt completed all 193 HiREST indexing
checkpoints but failed before predictions because benchmark records lacked the
generation identity now required by the search contract. Adding a shared
generation then exposed that the multi-video runner deleted the entire
generation before each video, leaving only the final video's records. A
two-video real smoke also caught official dataset filenames being passed into
the product's UUID4-only media ID fields.

The final adapter behavior now:

- derives stable UUID4-shaped internal media and generation IDs;
- retains official dataset video names at the file/evaluator boundary;
- removes only the current video's records when retrying inside a generation;
- supports documented DiDeMo media substitutions through the public CLI; and
- covers the generation/media-ID boundary in regression tests.

The temporary 1,037-video corpus, model-run indexes, repository clones, and ASR
copy were removed after recording the evidence above.

## Prepare and run

Install the optional adapters and initialize FFmpeg:

```powershell
uv tool install "vidxp[benchmarks,scene,speech]"
vidxp init
```

Prepare a declared DiDeMo smoke subset by zero-based official annotation
indices:

```powershell
vidxp benchmark prepare didemo --split test --annotation-indices 0,1,2
```

The command reads the pinned metadata needed to inspect the selection, displays
the maximum additional storage, destination free space, and any documented
replacement, then asks before persisting files. It resumes `.part` downloads,
validates each video with
FFprobe and a decoded frame, writes `preparation-manifest.json`, and prints the
complete benchmark command. Omit `--annotation-indices` for the full official
split.

The known corrupt Multimedia Commons object is never used or replaced
silently. If its annotation is selected, the plan identifies the archived
Wikimedia original, includes its 94,107,862-byte size, and records the SHA-1,
source URL, and generated `media-overrides.json`. `--yes` confirms the displayed
plan for automation; JSON output requires it.

For a HiREST smoke, a pair file is a JSON list:

```json
[
  {
    "prompt": "Make DIY Office Weapons",
    "video": "nWBuM3LNTcM.mp4"
  }
]
```

Prepare the released-ASR archive and a self-contained copy of that selection:

```powershell
vidxp benchmark prepare hirest `
  --split test `
  --pairs .\hirest-smoke-pairs.json
```

Preparation downloads the pinned annotations, categories, evaluator, and
released ASR archive, then extracts only the selected transcripts. No HiREST
video is downloaded by this adapter. Omit `--pairs` to prepare the complete
selected split.

By default, prepared data is stored below VidXP's platform-native application
data directory under `benchmarks/<benchmark>`. Use `--output-directory` to
choose another volume. The final line is deliberately a copy/paste command, so
the user does not have to assemble artifact paths manually.

The generated direct command can omit `--pairs` to produce all 776 official
test predictions. Test output is explicitly unscored; use `--split validation`
for locally evaluable official metrics.

## Shared run output

Both adapters produce:

```text
benchmark_runs/<benchmark>/<run_id>/
  manifest.json
  predictions.json
  timings.jsonl
  failures.jsonl
  evaluator.log
  index/
```

They also retain `ground_truth.subset.json`, the core completion marker, and
per-video checkpoints. Validation runs add `metrics.json`; held-out HiREST test
runs add `submission.summary.json`. Empty failure logs are created deliberately.

## 2026-07-27 legacy-provider executable smoke results

| Adapter | Declared subset | Actual path exercised | Official evaluator result |
|---|---|---|---|
| DiDeMo | Test annotation index `0`; one official downloaded video; frame stride `30` | Real CLIP scene indexing, max-within-chunk aggregation, strict serialization, pinned evaluator | Rank@1 `1.0`, Rank@5 `1.0`, mIoU `1.0` |
| HiREST | `Make DIY Office Weapons` / `nWBuM3LNTcM.mp4` | Released SRT parsing, five-word rechunking, real MiniLM/Chroma indexing, 0.8-duration known-video window, strict prediction validation | one unscored held-out test prediction |

The DiDeMo values establish execution and format correctness only. They are not
a paper score and were not used to choose max pooling.

The HiREST smoke pair was selected before the validation study and remained
held out during window selection. Its released `[0, 1]` bound is one of the
test split's placeholders, so the earlier `0.0` values were not valid held-out
metrics and are withdrawn. The prediction still establishes execution and
format correctness; the 193-pair validation run above supplies the locally
evaluable metric check.

The first HiREST evaluator run returned successfully, but VidXP's output parser
rejected the evaluator's `np.float64(...)` representation. The parser was
limited to normalizing those NumPy scalar wrappers, covered by a regression
test, and the same run then completed with an empty `failures.jsonl`. No metric
value or prediction was changed.
