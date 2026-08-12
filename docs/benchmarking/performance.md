# Latency benchmark protocol

Status: Ready

The latency benchmark (`vidxp benchmark index-latency`) measures indexing
throughput, per-stage timing, and peak memory using synthetic media generated
by FFmpeg on the caller's machine. It is designed for regression detection
between VidXP builds and for evaluating the latency impact of model or
architecture changes.

## Protocol

### Corpus generation

The benchmark generates deterministic synthetic clips using FFmpeg's `lavfi`
source filters:

| Parameter | Default | Notes |
|---|---|---|
| `--videos` | 1 | Number of synthetic clips |
| `--duration-seconds` | 8.0 | Wall-clock duration of each clip |
| `--fps` | 24 | Frame rate |
| `--resolution` | 320x180 | `WxH` format |
| `--audio-mode` | `none` | `none`, `sine`, or `flite` |
| `--input-mode` | `transcript` | `transcript` or `transcribe` |

Video is generated via `testsrc2` (colour bars + timestamp). When
`--input-mode transcript` and `dialogue` is enabled, a deterministic
synthetic transcript (seeded PRNG over a fixed English vocabulary) is
supplied without real transcription. When `input-mode transcribe` is
used, `--audio-mode flite` must also be set and libflite must be
available in the ffmpeg build.

### Indexing measurement

Each repetition runs the full indexing pipeline via `run_index()` with
`reset=True`. The following stages are timed by the existing manifest
timing infrastructure (`core/manifest.py:record_stage`):

| Stage | Modality | Measures |
|---|---|---|
| `frame_stream` | (all visual) | Decode throughput (frames/s) |
| `scene` | scene | SigLIP2 embedding (frames/s) |
| `actor` | actor | OpenCV detect + recognise (frames/s) |
| `visual_indexing` | all visual | Combined group wall time |
| `dialogue_indexing` | dialogue | Embedding throughput (phrases/s) |

Peak RSS is captured via `resource.getrusage(RUSAGE_SELF).ru_maxrss`
(POSIX only; `None` on Windows, reported in bytes on macOS, KiB on
Linux).

### Repetitions

When `--repetitions N` > 1, each repetition runs the full cycle
(generate once, index each time after `reset`). Results are reported
as mean, min, and max across all per-video per-repetition samples.

### Baseline comparison

Pass `--baseline <path-to-previous-report.json>` to compare the
current run against a prior report. For each stage present in both,
the delta ratio (`new_mean / old_mean - 1`) is computed. A stage with
a delta exceeding `--baseline-tolerance` (default 0.15 = 15%) is
flagged as a regression. The verdict is `fail` if any stage regressed,
else `pass`.

### Output

The benchmark writes its report to `run_directory/report.json` and
invokes `record_adapter_manifest` (embedding the corpus spec, device,
and result classification into the run's `manifest.json`).

Report schema:

```json
{
  "schema_version": 1,
  "benchmark": "latency",
  "run_id": "my-run",
  "corpus": { "videos": 1, "duration_seconds": 8.0, ... },
  "modalities": ["scene", "actor"],
  "device": "cpu",
  "repetitions": 1,
  "git": { "commit": "...", "dirty": false },
  "environment": { ... },
  "record_counts": { "scene": 8, "actor": 0 },
  "processed_frames": 8,
  "stages": {
    "scene": {
      "runs": 1, "mean_seconds": 2.1, "min_seconds": 2.1,
      "max_seconds": 2.1, "rate_per_second": 3.8
    }
  },
  "summary": {
    "wall_seconds": { "runs": 1, "mean_seconds": 5.0, ... },
    "peak_rss": { "unit": "bytes", "samples": 1, "value": 123456789 }
  },
  "baseline": null | { "stages": {...}, "regressions": [...], "verdict": "pass" }
}
```

## Limitations

- The synthetic video has no semantic scene content, so scene embeddings
  are representative of throughput but not retrieval quality.
- Actors are not present in `testsrc2` video; `actor` stage measures
  the per-frame face-detection overhead with zero detections.
- When `input_mode=transcript`, no real whisper transcription occurs;
  dialogue embedding is measured on a synthetic transcript.
- True transcription latency (`input_mode=transcribe`) requires a
  speech source (`--audio-mode flite`) and libflite in the FFmpeg
  build; the generated speech is a short fixed sentence and does not
  represent naturalistic conversation length or vocabulary.
- Peak RSS measures the whole-process peak, which includes Python
  overhead, loaded models, and Chroma state; it is not a pure
  indexing-stage measurement.

## Usage

```bash
# Default: single 8-second 320x180 clip, scene-only, 1 rep
vidxp benchmark index-latency --run-id my-baseline

# Scene + actor + dialogue (synthetic transcript), 3 reps, compare with baseline
vidxp benchmark index-latency \
  --run-id v2-compare \
  --modalities scene,actor,dialogue \
  --videos 2 \
  --duration-seconds 12 \
  --repetitions 3 \
  --json \
  --baseline benchmark_runs/latency/synthetic/my-baseline/report.json

# Real transcription (requires libflite in ffmpeg)
vidxp benchmark index-latency \
  --run-id transcribe-test \
  --modalities dialogue \
  --input-mode transcribe \
  --audio-mode flite \
  --device cpu
```

## Adding a new performance benchmark

1. Define the corpus parameters and any new modality combinations in
   the existing `run_latency` entry point.
2. Run the baseline and save its `report.json`.
3. Make your change (model swap, concurrency refactor, etc.).
4. Re-run with `--baseline <baseline-report.json>` and verify no
   regressions.
5. Commit the baseline report to a designated location (e.g.
   `docs/benchmarking/baselines/`) if it serves as a team reference.
