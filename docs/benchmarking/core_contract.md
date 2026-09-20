# Benchmark-ready core contract

This document describes the shared VidXP indexing and retrieval layer used before
benchmark-specific adapters are added. It does not define or replace any official
benchmark evaluator.

## Run configuration

`vidxp.core.IndexConfig` records the dataset, split, run ID, optional video ID,
enabled modalities, frame sampling, inference/write batch sizes, model
identifiers, and device. Normal benchmark runs are isolated at:

```text
benchmark_runs/<dataset>/<run_id>/
  manifest.json
  timings.jsonl
  failures.jsonl
  checkpoints/
  index/
  run.complete.json
```

`run.complete.json` is only written when every declared video has a valid
checkpoint and no video failed. A resumed run compares the input checksum and
configuration fingerprint, skips matching completed videos, and rebuilds an
interrupted video with deterministic upserts.

If a source contains both video and a supplied transcript, the run identity
includes both checksums. A caller-provided video checksum avoids re-reading the
video but does not suppress transcript hashing. The configuration fingerprint
captures inference, indexing, collection, dataset, split, and run semantics; it
does not change merely because the same run is relocated to another output or
storage directory. Checkpoint filenames are hashes of video IDs, so official IDs
cannot accidentally become platform-specific paths.

The CLI and Streamlit interface use `repositories/default/` beneath the
operating system's per-user VidXP data root. This local application repository
is separate from the explicit `benchmark_runs/` output above. Indexes created
with an older schema must be rebuilt; VidXP does not invent missing end
timestamps, video IDs, or source IDs.

## Indexing API

```python
from vidxp.core import IndexConfig, VideoSource
from vidxp.core.runner import run_index

config = IndexConfig(
    dataset="example",
    split="test",
    run_id="scene-1fps",
    generation_id="123456781234423481234567890abcde",
    enabled_modalities=("scene",),
    capability_options={
        "scene": {"batch_size": 32, "sample_fps": 1.0},
    },
    storage_batch_size=256,
)

manifest = run_index(
    [
        VideoSource(
            video_id="223456781234423481234567890abcde",
            path="videos/001.mp4",
        ),
        VideoSource(
            video_id="323456781234423481234567890abcde",
            path="videos/002.mp4",
        ),
    ],
    config,
)
```

Released timestamped ASR can be indexed without running a transcription model or decoding video:

```python
source = VideoSource(
    video_id="223456781234423481234567890abcde",
    transcript=(
        {"text": "first timestamped span", "start": 0.0, "end": 2.4},
        {"text": "second timestamped span", "start": 2.4, "end": 5.0},
    ),
)

config = IndexConfig(
    dataset="example",
    split="test",
    run_id="released-asr",
    generation_id="423456781234423481234567890abcde",
    enabled_modalities=("speech",),
)

run_index([source], config)
```

Scene-only runs do not load a transcription or actor model. Supplied-transcript
speech runs load the speech encoder but do not decode video. Actor-only runs do
not load scene or transcription models. Scene inference, speech encoding,
and Chroma writes use their configured batch sizes. Cancellation is cooperative
and is checked between batches. The Streamlit process exposes cancellation for
indexing workers it started and reports that the current batch must finish
first. The shared file lock also lets the UI detect an active CLI or
separate-process run, although one process cannot cancel a run owned by another
process.

Scene and actor indexing share one video probe and one sampled-frame stream when
both are enabled. Each materialized frame is converted to RGB once and then fed
to independently sized scene and actor batches. Actor detections are written
incrementally; clusters below `actor_min_detections` are removed after clustering
instead of retaining every detection in process memory.

Scene `sample_fps` is converted from the probed source FPS into a deterministic
frame cadence. A source below the requested rate uses every available frame
without duplication. `frame_stride` independently controls actor and legacy
visual capability sampling. When scene and actor are both enabled, the decoder
materializes the union of their required frames once and routes each capability
only its own cadence. Inter-frame codecs may still require backend-internal
decoding, so sampling is not claimed to reduce codec work in direct proportion
to the cadence. Manifests distinguish `source_frames_advanced`, unique
`sampled_frames`, and the sum of per-modality `frame_operations`.

## Stored identity and metadata

Generation-aware records have a deterministic escaped source ID:

```text
generation_id:run_id:video_id:modality:local_id
```

Each component is encoded before joining, so a colon or Unicode character inside
source metadata cannot collide with the separator. Product media and generation
IDs are lowercase UUID4 hex. Dataset adapters retain official video keys at
their input/evaluator boundary and deterministically map them to valid internal
IDs.

- Speech records store text, start/end, phrase ID, video ID, modality, source
  ID, dataset, split, and run ID.
- Scene records store frame index, timestamp, start/end, FPS, duration, video ID,
  modality, source ID, dataset, split, and run ID.
- Actor records store detection ID, cluster ID, frame index, timestamp, bounding
  box coordinates, video ID, modality, source ID, dataset, split, and run ID.

## Retrieval API

```python
from vidxp.capabilities.scene.operations import search_scene

result = search_scene(
    "a person cuts bread",
    config=config,
    top_k=10,
)

for hit in result.hits:
    print(
        hit.rank,
        hit.video_id,
        hit.start,
        hit.end,
        hit.raw_distance,
        hit.score,
        hit.source_id,
    )
```

Passing a media UUID through `video_id` restricts retrieval to one video; omitting it
searches the run corpus. Results are deterministically ordered by raw distance and
then source ID. Scene vectors and, by default, speech vectors are normalized
before the explicitly configured Chroma distance (`vector_distance`, default
`l2`), making the default ordering cosine-equivalent. The distance is stored in
the run configuration and Chroma collection rather than relying on Chroma's
implicit default.
`raw_distance` is still the unmodified value returned by Chroma. `score` is
exactly `-raw_distance`, so higher is better; it is deliberately not described
as a probability or calibrated relevance value.

Generated query IDs are deterministic over dataset, split, run ID, modality, and
query text. The same text in two benchmark runs therefore cannot silently collide.
Search rejects records that lack dataset, split, run ID, video ID, modality, and
source ID provenance, in addition to the modality-specific interval metadata.

`serialize_predictions()` writes the generic query-to-ranked-hits contract while
preserving queries with zero hits. The implemented official DiDeMo and HiREST
serializers remain isolated in their benchmark adapters and do not alter official
evaluator logic.

## Deliberate remaining boundaries

The core deliberately does not provide generic benchmark-specific dataset
loaders, interval aggregation, prediction serializers, or evaluator invocations.
DiDeMo and HiREST now implement those operations in their own adapters; later
benchmarks must follow the same separation and their own official protocols.

Resume is currently per video, not from the middle of a partially indexed video.
Frame timestamps are derived from frame index and reported FPS, so a
variable-frame-rate benchmark needs a timestamp-aware decoder adapter before its
localization result can be claimed as exact. Chroma returns the requested
approximate-nearest-neighbour candidate set; deterministic sorting stabilizes
those returned candidates but does not turn approximate retrieval into an exact
full-corpus ranking.
