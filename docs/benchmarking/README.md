# VidXP benchmarking

This directory is the entry point for evaluating VidXP against published
benchmarks. It records what was selected, what has actually been run, how the
results compare, and which claims the evidence supports.

Benchmark work is separate from writing or tuning the paper itself. For
installation and product usage, start with the main
[VidXP README](../../README.md).

## Current status

| Area | Status | What it means |
|---|---|---|
| Shared benchmark support | Complete | Stable IDs, time ranges, metadata, top-k retrieval, isolated runs, checkpoints, and prediction files are implemented |
| Guided input preparation | Complete | `vidxp benchmark prepare` estimates and confirms downloads, verifies pinned artifacts, validates DiDeMo media, resumes partial transfers, and prints the runnable benchmark command |
| DiDeMo visual localization | Legacy full result + current smoke | The legacy CLIP stack completed 4,021 official test queries over 1,037 videos; the current SigLIP2 stack passed a one-annotation real execution smoke |
| HiREST transcript localization | Legacy full result + current smoke | The legacy MiniLM stack scored all 193 validation pairs; current Qwen3 passed a two-video real execution smoke; 776 released test predictions remain unscored because their public bounds are placeholders |
| LongVALE combined evaluation | Next | Build the visual-plus-speech adapter and validate one evaluation archive before scheduling the full run |
| Actor clustering | Data-gated | The preferred BBT/Buffy evaluation still requires lawful access to the source episodes |

Read [current results](results.md) for the scores, plain-language metric
definitions, honest comparisons, and the next benchmark decision.

## Find the right document

| If you need to… | Read |
|---|---|
| Understand how VidXP performed | [Current results](results.md) |
| Reproduce DiDeMo or HiREST | [Adapter validation ledger](adapter_validation.md) |
| Understand the benchmark-ready Python structure | [Core contract](core_contract.md) |
| See which benchmarks exist and what each measures | [Benchmark catalog](benchmark_catalog.md) |
| Find exact published competitor scores | [Published comparison results](published_results.md) |
| Review the relevant papers | [Research-paper inventory](research_papers.md) |
| Audit what was checked in each paper | [Paper-validation ledger](paper_validation.md) |
| Review real runtime checks | [Runtime-validation ledger](runtime_validation.md) |

The [original direction](direction.md) and
[pre-implementation readiness assessment](execution_readiness.md) are retained
as dated planning records. They explain how the benchmark work was selected, but
they are not the current task list.

## Current benchmark position

No single published benchmark covers dialogue retrieval, scene retrieval, and
actor clustering together.

The retained full DiDeMo and HiREST results establish separate legacy-provider
visual and transcript baselines. Current SigLIP2 and Qwen3 checks establish
adapter/runtime compatibility only; they do not yet provide full-corpus quality
comparisons. LongVALE is the next combined test because it asks a system to find
described events in long videos using visual, speech, and general audio
evidence. VidXP can currently contribute visual and speech evidence; it does
not recognize general sounds such as music, alarms, or barking. Any LongVALE
result must keep that limitation visible.

## Evidence rules

- Use the official data split, output format, and evaluator.
- Keep trained competitors separate from off-the-shelf systems.
- Record the exact code revision, model settings, predictions, failures, and
  evaluator output.
- Label validation results separately from held-out test results.
- State when supplied transcripts replace VidXP transcription.
- Do not turn a missing capability into an unreported dataset filter.

## Historical material

[Legacy benchmarking methodology](../benchmarking_research.md) remains at its
original path and filename for provenance. It contains an earlier Urdu-specific
assumption and a custom-corpus direction that are not part of the current plan.
