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
| Environmental-sound retrieval | FineLAP selector rejected; long-audio replacement identified | The current selector scored zero on four held-out tasks. DCASE 2026 establishes query-conditioned sequence-to-interval models as the direct task; released and winning candidates are now separated by artifact/runtime readiness. Do not run the paid agent comparison against the current selector. |
| LongVALE combined evaluation | Pilot not run | The prepared paired tasks can measure evidence quality, localization, tokens, time, cost, and tool use after maintainer approval |
| Codex MCP ablation | Development smoke traced | One paired task verified the harness and exposed a fixed-window boundary error; the 54-run held-out pilot has not run |
| Actor clustering | Data-gated | The preferred BBT/Buffy evaluation still requires lawful access to the source episodes |

Read [current results](results.md) for the scores, plain-language metric
definitions, honest comparisons, and the next benchmark decision.

## Find the right document

| If you need to… | Read |
|---|---|
| Understand how VidXP performed | [Current results](results.md) |
| Compare consolidated run metrics and machine profiles | [Metric database](metric_database.md) |
| Reproduce DiDeMo or HiREST | [Adapter validation ledger](adapter_validation.md) |
| Understand the benchmark-ready Python structure | [Core contract](core_contract.md) |
| See which benchmarks exist and what each measures | [Benchmark catalog](benchmark_catalog.md) |
| Understand the current product and evaluation choices | [Evidence retrieval direction](model_selection.md) |
| See exactly which paper-derived ideas are in the product | [Research adoption record](research_adoption.md) |
| Run the Codex MCP-on/MCP-off experiment | [Codex agent ablation](agent_ablation.md) |
| Find exact published competitor scores | [Published comparison results](published_results.md) |
| Review the relevant papers | [Research-paper inventory](research_papers.md) |
| Audit what was checked in each paper | [Paper-validation ledger](paper_validation.md) |
| Review real runtime checks | [Runtime-validation ledger](runtime_validation.md) |

The [original direction](direction.md) and
[pre-implementation readiness assessment](execution_readiness.md) are retained
as dated planning records. They explain how the benchmark work was selected, but
they are not the current task list.

## Current benchmark position

No single published benchmark covers speech retrieval, scene/action retrieval,
environmental-sound retrieval, exact temporal boundaries, and actor clustering
together.

The retained full DiDeMo and HiREST results establish separate legacy-provider
visual and transcript baselines. Current SigLIP2 and Qwen3 checks establish
adapter/runtime compatibility only; they do not yet provide full-corpus quality
comparisons. VidXP now contributes visual, speech, and FineLAP sound evidence,
including global windows and dense timestamps for non-speech events.

The first Codex MCP development pair found the requested opening event but
returned an interval two seconds too long. It also finished faster and used
fewer total tokens than direct inspection, although its estimated cost was
slightly higher because more input was uncached. Later local controls exposed a
separate FineLAP integration error: global clip and dense activation records
were cross-ranked. Separating those representations is correct, but the
replacement selector also failed: its four held-out sound tasks produced no
target-overlapping final top-three result. The agent comparison is blocked on a
sound-search correction, not pending as if this selector were validated.

The next paid paired run should test the product claim directly only after the
sound provider is corrected: whether
VidXP gives the agent enough inspectable evidence to reach a similarly grounded
answer with fewer tokens, less time, or fewer media-inspection calls. IoU and
boundary errors remain important diagnostics, not the entire product decision.
See [current model direction](model_selection.md) and the
[research adoption record](research_adoption.md).

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
