# Benchmark execution readiness

> **Historical assessment:** Statements below that generic sound was unsupported
> accurately describe the implementation when this assessment was written. The
> active [multimodal model direction](model_selection.md) now records the shipped
> FineLAP sound layer and places LongVALE and FLARE adapter validation next.

Collection index: [Benchmarking research](README.md)

Status: Historical pre-implementation assessment

Established: 2026-07-26

Baseline before this reassessment: commit `4607f9d`

This assessment explains why the benchmark-ready refactor was feasible. Its
repository limitations and execution sequence describe the code before that
work was implemented. They are not the current task list. See
[current results](results.md), the [core contract](core_contract.md), and the
[benchmark index](README.md) for the live state.

## Corrected premise

VidXP's current return shape is not a fixed research constraint. The implementation
may be changed to support published benchmark protocols when the change is ordinary
engineering rather than a new learned capability.

Allowed benchmark plumbing includes:

- stable dataset, split, run, video, clip, frame, phrase, and face-track IDs;
- configurable top-k results instead of top-1;
- raw distances plus a documented monotonic score;
- start and end timestamps, frame/shot boundaries, and supplied metadata;
- collection namespaces and dataset/video filters;
- deterministic point-to-clip, point-to-window, shot, or video aggregation;
- temporal de-duplication or non-maximum suppression;
- fixed sliding-window proposals;
- benchmark-specific prediction serializers and evaluator invocation;
- non-learned score or rank fusion over existing scene and dialogue outputs;
- modality-specific indexing, batching, resumability, and timing instrumentation.

These changes do not alter the benchmark task and do not invalidate comparison with
published baselines.

Material capability changes remain separate:

- training or fine-tuning a retrieval or temporal-localization model;
- adding OCR, generic sound-event recognition, speaker diarization/identification,
  learned fusion, or a multilingual replacement encoder;
- adding face/body/voice fusion or a new actor-tracking model.

A benchmark may still be run when VidXP lacks one evidence channel. Unsupported
queries can remain in the official denominator and produce poor scores. The result
must describe the missing capability; it must not claim full modality coverage.

## Repository complexity finding

The baseline system was not protected by a complex package API. It was a small
application with direct Typer commands and a thin Streamlit caller. The later
core refactor replaced that structure.

The main benchmark-facing limitations are localized:

- numeric Chroma IDs restart for every video;
- voice records store only `start`;
- scene records store only `time`;
- search hardcodes `n_results=1` and discards distances;
- indexing always runs dialogue, scene, and actor work together;
- frame inference and database writes are unbatched;
- WhisperX is loaded inside each indexing call;
- no dataset/run namespace or benchmark serializer exists.

Nothing in that list requires a new model.

## Classification used from this point

Benchmark fit and execution state are separate:

| Axis | Class | Meaning |
| --- | --- | --- |
| Engineering | **A — adapter** | Valid official predictions can be produced with existing encoders plus deterministic plumbing |
| Engineering | **B — material** | A faithful claim requires a new model, learned method, or new product capability |
| Operations | **Ready** | Public artifacts are sufficient to begin a smoke run |
| Operations | **Gated** | Agreement, license, media survival, storage, or compute must be resolved |
| Operations | **Blocked** | A necessary corpus, evaluator, or artifact is not presently obtainable |

“A/Ready” does not mean VidXP will score well. It means the official evaluator can
measure the current method without changing the task.

## Shared implementation slice

The minimum reusable retrieval result is:

```text
query_id -> [
  {
    video_id,
    start,
    end,
    score,
    raw_distance,
    modality,
    source_id
  },
  ...
]
```

The minimum indexing changes are:

1. Split audio, scene, and actor indexing into selectable functions.
2. Accept `dataset`, `split`, `run_id`, and stable `video_id`.
3. Use collision-safe IDs such as
   `run_id:video_id:modality:local_index`.
4. Store voice `{start, end, text, video_id}` and scene
   `{frame_index, time, video_id, fps, duration}`.
5. Make `top_k`, collection name, and optional `video_id` filter configurable.
6. Return ordered records with metadata, distances, and scores.
7. Batch model inference and Chroma writes and reuse loaded models.
8. Add fixed aggregation and serialization outside the core encoder code.

A scene-only implementation slice is approximately one to two developer days. A
reusable scene/dialogue/corpus contract with tests, batching, and serializers is
approximately three to five developer days. Dataset download and indexing time are
not included.

## Revised executable shortlist

| Benchmark | Engineering | Operations | What is executable |
| --- | --- | --- | --- |
| DiDeMo | A, small | Ready; test media 4.958 GiB | Official 21-candidate moment ranking with Rank@1/5 and mean IoU |
| HiREST known-video moment retrieval | A, small | Ready from 6.83 MiB released-ASR archive | Official local validation metrics for 193 pairs; 776 held-out test predictions are generated but cannot be scored from placeholder test bounds |
| HiREST video retrieval | A, small/medium | Gated by complete 4,282-video pool | Official video R@1/5/10/50 only after all 1,391 annotated and 2,891 negative candidates receive consistent evidence |
| QVHighlights | A, medium | Gated by 133.863 GiB all-splits raw archive/CPU cost | Complete result needs scored intervals and one saliency score per two-second clip on an explicitly named public-label release |
| Charades-STA | A, medium | Gated by data agreement | Official ranked interval retrieval using fixed-grid windows on either the labelled original or filtered split |
| MSR-VTT 9K/1K-A | A, small | Ready after 6.103 GiB full-archive download and 1K test extraction | Official frozen whole-video text retrieval from pooled frame scores on the named 1K-A gallery; no training split needed |
| QuerYD | A, small/medium | Protocol/reference artifacts ready; raw video and narration audio unresolved | True VidXP paragraph-video and supplied-proposal retrieval is media-gated; released features support only a foreign-feature reference check |
| TVR `sub-only`, `video-only`, `video+sub` | A, medium | Gated by lawful raw TV clips with audio | Official SVMR/VCMR/VR once media exists |
| TVR-Ranking | A after TVR | Gated by TVR media and exact license | Official graded ranked moment retrieval |
| BCL on BBT/Buffy | A/medium clustering adapter | Reference pipeline 523.36 MiB; VidXP run gated by 12 lawful episodes | WCP/NMI against released labels after frozen track alignment and VidXP clustering; the released script does not accept arbitrary cluster IDs |
| LongVALE | A/medium with fixed late fusion and interval adapter | Raw artifacts reachable; compliance and CPU/runtime pilot required | One interval per each of 13,867 evaluation queries from 40.523 GiB raw archives; no training download, checkpoint, or generic-audio claim |
| FLARE | A/medium | Reachable full revision, 66.267 GiB; runtime gated | Official caption-to-clip/video retrieval and clip-level simulated-query retrieval; unsupported sound-only queries remain measured failures |
| TRECVID AVS | A/medium | Gated by agreement and roughly 1.6 TB V3C2 | Archived 2024/2025 top-1,000 master-shot ranking and mean xinfAP using exact-year topics/qrels |
| MultiVENT 2.0 | A/large operational adapter | Gated by about 1.93 TB and compute | Official ranked-video run using scene, ASR, and supplied descriptions; embedded-text/OCR unsupported and generic acoustic audio is not a benchmark channel |
| VectorDBBench | A, configuration-dependent | Ready after custom case generation | Chroma recall, indexing time, latency, and QPS diagnostic only |

## Validated execution footprints

Download bytes, extracted bytes, foreign precomputed features, true VidXP inputs,
and peak working space are different quantities. The following table keeps them
separate; an unknown value remains unknown rather than being inferred from an
archive label.

| Benchmark/mode | Verified fixed artifacts | What VidXP actually needs | Working-space consequence |
| --- | --- | --- | --- |
| [DiDeMo official test](https://github.com/LisaAnne/LocalizingMoments) | 1,037 reachable videos, 5,323,396,782 bytes (4.958 GiB); validation is 4.919 GiB | Test annotations, raw test videos, VidXP frame embeddings, and the official evaluator | Download is small enough to begin; index/cache overhead must still be measured |
| [HiREST known-video moment](https://github.com/j-min/HiREST) | `ASR.zip` 7,160,738 bytes (6.83 MiB); 3,375 SRTs expand to 16.44 MiB | Released SRTs for the 776 test pairs, re-chunked and re-embedded by VidXP | Immediately executable without raw video; measures retrieval/indexing while ASR is held fixed |
| [HiREST video retrieval](https://github.com/j-min/HiREST) | 244.95 MiB MiniLM, 3.451 GiB 1-fps visual, and 9.645 GiB 32-frame visual feature packs | For an end-to-end transcript run, all 4,282 candidate videos and consistent VidXP transcription; negative-only candidates lack SRT | Raw bytes and survival are not fixed; foreign visual/MiniLM packs do not establish VidXP end-to-end performance |
| [QVHighlights](https://github.com/jayleicn/moment_detr/tree/main/data) | One all-splits raw archive: 143,734,787,897 bytes (133.863 GiB); documented feature release about 8 GB | Raw archive, annotations/evaluator, VidXP clip embeddings, saliency values, and index | Extraction requires more than 133.863 GiB before frames, embeddings, and index; exact peak is unverified |
| [MSR-VTT 9K/1K-A](https://arxiv.org/abs/1604.01775) ([preparation repository](https://github.com/m-bain/frozen-in-time)) | Raw 10K-video ZIP 6,552,768,292 bytes (6.103 GiB); split/caption ZIP 3.88 MiB | The 1,000 JSFUSION test-gallery clips, their captions/test list, VidXP embeddings, and evaluator; 9K training data are not needed | Selectively extract the 1K gallery and measure it. Roughly 12.2 GiB applies only to the conservative strategy of retaining the archive plus all 10K extracted videos |
| [QuerYD released protocol/reference package](https://www.robots.ox.ac.uk/~vgg/data/queryd/) | Fixed metadata, query transcripts, timestamps, confidence data, and foreign features total 840,716,055 bytes (801.77 MiB) | Raw source videos and VidXP visual embeddings for an official VidXP result; narrator WAVs only if testing the query-side WhisperX path | Downloader representation and YouTube survival make raw bytes variable; the fixed package alone cannot produce a VidXP score and sampled audio endpoint was unavailable |
| [BCL reference pipeline](https://github.com/makarandtapaswi/BallClustering_ICCV2019) | Tracks 5.27 MiB plus SE-ResNet features 518.09 MiB, 523.36 MiB total | Reference package only reproduces BCL; VidXP needs all 12 lawful episodes, its own features/clustering, released labels, and track alignment | Raw episode size is unpublished; the 256→64 BCL MLP is incompatible with VidXP 128-D dlib vectors |
| [LongVALE official evaluation](https://huggingface.co/datasets/ttgeng233/LongVALE) | Raw ZIPs 40.523 GiB; raw MP4 payload 40.664 GiB; annotation 4.52 MB; feature ZIPs 0.544 GiB/0.587 GiB extracted | Raw evaluation MP4s, 13,867 annotations, VidXP encoders/index, one interval per query, evaluator | ZIPs plus extracted MP4s are 81.186 GiB before temporary audio, embeddings, and index; pilot those additions |
| [FLARE full release](https://huggingface.co/datasets/YqjMartin/FLARE) | 14 ZIPs plus nine JSONLs are 71,152,739,766 bytes (66.266 GiB); full revision including other files is 71,153,909,874 bytes (66.267 GiB) | All relevant segmented MP4s/JSONLs, VidXP encoders/index, and an adapter to the model-specific harness | If archives are retained, use roughly 132 GiB as a planning floor before index/cache; exact extracted peak is unpublished |
| [VectorDBBench custom case](https://github.com/zilliztech/vectordbbench) | No fixed “small” corpus; built-ins span 50K×1,536 to 100M×768 | Train/test/exact-neighbor Parquet files and the Chroma adapter | Raw float32 lower bounds: MiniLM-384 is 146.48 MiB/100K or 1.43 GiB/1M; CLIP-512 is 195.31 MiB/100K or 1.91 GiB/1M, before index overhead |

## Benchmark-specific corrections

### DiDeMo

DiDeMo is not blocked by point timestamps. VidXP can score frames, aggregate them
into the six published five-second chunks, score all 21 legal contiguous moments,
and emit the official ranked list. Respect the annotation `num_segments` and the
first six chunks even when a source video exceeds 30 seconds. No temporal model,
training corpus, or checkpoint is needed.

This remains the first visual benchmark because it validates nearly all shared
scene infrastructure at low cost. All 1,037 official test videos are currently
reachable and total 4.958 GiB; the full corpus is not required for a test-only
VidXP evaluation.

### HiREST

HiREST is executable immediately only for transcript-backed known-video moment
retrieval:

1. ingest the released ASR and timestamps;
2. rechunk and embed it with VidXP's MiniLM path;
3. project all known-video phrase scores onto a one-second timeline and rank the
   validation-frozen 0.8-duration window;
4. serialize validated predictions for the 776 held-out test query–video pairs
   as an unscored submission artifact.

This measures VidXP's chunking, embedding, vector indexing, and retrieval while
holding ASR constant. It does **not** support official video retrieval: that test
has 1,391 annotated candidates plus 2,891 negative-only candidates, and the
negative distractors have no released SRT. A valid transcript-backed video
retrieval run must obtain all 4,282 videos and transcribe them consistently. The
official baseline's use of Whisper and `all-MiniLM-L6-v2` still makes known-video
moment retrieval a useful same-stack benchmark.

### QVHighlights and Charades-STA

Both can be evaluated without a learned proposal model. Fixed clip grids or
predeclared multi-scale windows produce valid non-zero intervals. Deterministic
aggregation and temporal NMS are baseline logic, not benchmark manipulation.

For complete QVHighlights evaluation, output non-zero scored
`pred_relevant_windows` and one saliency value for every two-second clip. Reporting
only the moment metrics is acceptable only as a labelled component experiment.
Pin `highlight_test_with_gt.jsonl` at repository commit
`b7e553ac3b0c898ee6b85e03ee507c064eab89ca`, 824,658 bytes, SHA-256
`bd50ec6bb5dd3f72571126ba5fdc7418efdd9219a9487984e483a02e2ce5d493`;
do not mix it with the original private CodaLab test or the 1,434-row `val-filt`
split. A declared subset reduces preprocessing/runtime but not the 133.863 GiB
monolithic download unless the subset media are sourced independently.
QVHighlights' other issue is download/indexing cost. Charades-STA's main issue is
the dataset agreement and its narrow staged-indoor domain.

### TVR and TVR-Ranking

These are algorithmically compatible after the shared corpus adapter. The real
blocker is raw copyrighted media with original audio, not top-k or interval output.
If lawful media is secured, run `t`, `v`, and `vt` separately and preserve the
official evaluator.

### QuerYD

QuerYD's queries, splits, labels, oracle proposals, and Collaborative Experts
features are available, but that does not make a VidXP run executable. The released
transcripts are volunteer description/query annotations, not a transcript gallery
for the source videos. An official VidXP paragraph-video or proposal-ranking result
requires surviving raw YouTube videos processed by VidXP's visual path; using the
released foreign features is only a protocol/reference check. The narrator WAV
endpoint was not confirmed at file level, so query-side WhisperX evaluation is a
separate gate. Narration rather than in-scene dialogue remains a scientific caveat.

### Actor clustering

The actor lane has a different external constraint. VidXP can emit per-detection
cluster IDs and an adapter can assign each benchmark face track to a predicted
cluster using timestamp/bounding-box matching and a frozen majority rule. WCP,
NMI, and predicted cluster count can then be computed against the released labels.

That adapter is not the released BCL `evaluate.py` path. The script consumes the
authors' 256-D SE-ResNet features, applies a learned 256→64 MLP and learned HAC
stopping rule, and only then reports WCP/NMI; it does not accept arbitrary VidXP
cluster IDs. Running the supplied 523.36 MiB package verifies/reproduces BCL but
does not evaluate VidXP. A true run needs lawful raw BBT/Buffy episodes, VidXP's
own 128-D dlib features and frozen clustering, plus track alignment to the released
labels. Hannah remains an alternative if its agreement and movie access are
approved.

### LongVALE and FLARE

Fixed late fusion over existing scene and dialogue rankings is permitted baseline
logic. It makes official retrieval or temporal-grounding runs technically possible.
VidXP still lacks generic sound-event recognition; the result must say so and must
not be described as full omni-modal coverage.

LongVALE is the stronger peer-reviewed combined benchmark. Its full Hugging Face
repository is about 254 GB, but a VidXP evaluation does not require the training
split, training features, instruction-tuning data, or LongVALE-LLM checkpoints.
At dataset revision `18889b01886e30c36b0d1c650ac4439ad460ee73`, the required
raw evaluation release is nine ZIP files totalling 43,511,050,176 bytes
(40.52 GiB), and `longvale-annotations-eval.json` is 4.52 MB. The released
evaluation features total 583,729,907 bytes (0.54 GiB). They are precomputed inputs
to the official LongVALE-LLM stack, not a complete reproduction package; the
official path additionally requires its base model/projector/stage weights, CUDA
environment, and evaluator. In all cases they bypass VidXP's own encoders.

The evaluator expects one interval per query; the reference path represents start
and end as integer percentages from 0 to 99. Top-k response support alone is not
enough: VidXP needs a frozen point-to-interval or proposal rule and must serialize
the top interval in the required coordinate system.

The raw MP4 payload is 43,662,117,997 bytes (40.664 GiB), so retaining ZIPs plus
extracted media is already 81.186 GiB. That is still not peak working space.
Before the full run, process one ZIP and record extracted bytes, temporary audio,
Chroma growth, sampled-frame count, wall time per video hour, and whether archives
can be deleted incrementally. The current all-frame, CPU-only loop is not an
acceptable basis for extrapolating the 75.6-hour evaluation split without that
pilot. No LongVALE checkpoint is required for the VidXP run.

FLARE's media/data ZIP+JSONL artifacts are 66.266 GiB; the entire pinned revision,
including other repository files, is 66.267 GiB rather than an ambiguous “about
71 GB.”
It remains a 2026 preprint with model-generated, rank-filtered queries. Its audio
queries include speech, music, and sound events, so a speech-only slice is partial
coverage and must not be called full audio evaluation.

### Large-corpus tests

TRECVID and MultiVENT are technically executable after adapters. Their present
deferral is operational: agreements, terabyte-scale storage, resumable ingestion,
and current CPU-only runtime. API shape is no longer listed as the blocker.

## Planned execution path at the time of assessment

1. Implement and test the shared stable-ID, interval, top-k, score, filter, and
   serializer contract.
2. Run a small DiDeMo smoke set, then its full validation protocol.
3. In parallel, run HiREST's released-ASR known-video moment protocol; do not claim
   its video-retrieval task until the complete 4,282-video evidence pool exists.
4. Add the local timing harness and VectorDBBench diagnostic while those corpora
   index.
5. Resolve TVR and actor raw-media access before writing those adapters.
6. Run a one-archive LongVALE capacity/runtime pilot, then schedule its official
   evaluation split as the core combined benchmark if the measured budget passes.
7. Move to QVHighlights validation after confirming its storage/runtime budget.
8. Treat FLARE as a secondary combined stress test, with modality limitations
   declared in advance.

This order yields official metrics quickly while keeping the preferred but
access-gated TVR and BCL targets active.
