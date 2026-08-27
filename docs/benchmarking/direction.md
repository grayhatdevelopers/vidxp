# Benchmarking direction and research source of truth

Collection index: [Benchmarking research](README.md)

Status: Historical founding brief; discovery phase complete

Established: 2026-07-25

Applies to: VidXP / ActorDB paper benchmarking work

> **Current direction:** The 2026-08-27
> [multimodal model decision](model_selection.md) supersedes this brief's
> implemented-capability-only sequencing. Environmental-sound retrieval has now
> been implemented; full vision/sound/speech evaluation is the next phase.
> This file remains the historical discovery contract.

This document preserves the rules used to start the benchmark research. It
predates the benchmark-ready core and completed DiDeMo/HiREST runs. Use
[current results](results.md) and the [collection index](README.md) for the
active status and next work.

## Objective

Identify which published benchmarks, datasets, metrics, and baseline implementations
can credibly evaluate the capabilities that exist in the current VidXP repository.

The first milestone is a researched and evidence-backed benchmark shortlist. We will
not begin by building a custom dataset or a general benchmark harness. Those steps
must be justified by gaps found during the published-benchmark audit.

This work produces measurements and reproducible benchmark artifacts for later use
in the report. Editing or tuning the paper itself is outside this workstream.

## Current evaluation scope

Research should focus on the capabilities implemented by the current project:

1. Dialogue or speech-query retrieval to a video timestamp.
2. Natural-language scene retrieval to a video timestamp.
3. Within-video actor or face clustering and actor highlighting.
4. Indexing and query efficiency where a published system benchmark or defensible
   measurement convention exists.

Language is not a primary benchmark axis. Record the language of evaluated material,
but do not introduce an Urdu-specific or multilingual benchmark unless the published
benchmark landscape or an explicit project claim makes it necessary.

Do not treat aspirational features from historical reports as implemented scope.
Examples include speaker diarization, script alignment, combined multimodal search,
keyframe or shot detection, and distributed indexing.

## Implementation flexibility

The current CLI and return types are not fixed research constraints. Benchmark work
may add stable corpus IDs, top-k results, scores, richer metadata, start/end
intervals, filtering, deterministic window aggregation, serializers, timing hooks,
and non-learned late fusion over existing scene and dialogue rankings.

These are ordinary adapters. A candidate was not rejected merely because the
pre-refactor application returned one timestamp or stored too little metadata.

New trained models or unsupported capabilities remain material changes. Examples
include OCR, generic sound-event recognition, speaker identification, learned
fusion, multilingual encoder replacement, and face/body/voice fusion.

## First direction: audit published benchmarks

The first phase is discovery, verification, and classification of published work.
Research must establish what can actually be obtained and executed, not merely list
papers containing similar terminology.

Use these research lanes:

### Lane A: dialogue and temporal retrieval

Find benchmarks for speech-backed video search, subtitle or transcript retrieval,
text-to-moment retrieval, point or interval localization, and corpus-wide video
moment retrieval.

### Lane B: visual scene retrieval

Find benchmarks for text-to-video retrieval, text-to-clip retrieval, text-to-frame
or moment localization, long-video search, and zero-shot CLIP-style retrieval.

### Lane C: actor and face clustering

Find benchmarks for face clustering in video, unknown-identity clustering,
face-track clustering, within-video character grouping, detection coverage, and
cluster fragmentation or false merging.

Face verification benchmarks are relevant only as component provenance. They are
not actor-clustering benchmarks unless their protocol directly evaluates clustering.

### Lane D: system and efficiency evaluation

Find comparable video indexing or multimodal search systems with documented
indexing time, query latency, throughput, resource use, corpus scale, and hardware.
Determine whether their protocols are reproducible or whether their numbers are
context only.

## Required benchmark record

Every candidate benchmark must have a record containing:

| Field | Required evidence |
| --- | --- |
| Name | Paper, benchmark, or dataset name |
| Source | Primary paper and official project or dataset links |
| Year and venue | Publication year and venue |
| Task | Exact task being evaluated |
| Inputs | Video, frames, audio, transcript, subtitles, text query, or identity labels |
| Output unit | Video, clip, interval, point timestamp, frame, face track, or cluster |
| Dataset | Name, scale, language, domain, and split protocol |
| Access and license | Availability, application requirements, redistribution limits, and license |
| Metrics | Exact metric names and relevance or matching definitions |
| Baselines | Published baseline methods relevant to VidXP |
| Code and models | Official implementation, checkpoints, environment, and maintenance state |
| Reproduction cost | Expected storage, preprocessing, compute, and runtime |
| VidXP fit | Matching capabilities and important mismatches |
| Interpretation | What a VidXP result on this protocol would demonstrate |
| Non-claims | What the result would not demonstrate and must not be used to claim |
| Verdict | Directly runnable, adaptable, reference-only, or irrelevant |
| Confidence | Confirmed, partially confirmed, or unresolved |

Claims about access, metrics, code, or results should be supported by primary
sources. Search-result snippets and secondary summaries are not sufficient.

## Classification rules

Each candidate receives exactly one initial verdict:

- **Directly runnable:** VidXP can be evaluated on the benchmark with its published
  dataset and metric protocol without changing the task definition.
- **Adaptable:** the data or evaluation code is usable, but a documented adapter is
  needed for VidXP predictions or retrieval units.
- **Reference-only:** the work is useful architectural or performance context, but
  its published numbers cannot be compared directly with VidXP.
- **Irrelevant:** the apparent similarity does not survive protocol inspection.

Published results become a direct baseline only when the competing method and VidXP
are run on the same frozen data, queries, judgments, output units, and metrics.
Similar metric names do not make results comparable.

Every serious candidate also receives two execution labels:

- **Engineering A — adapter:** existing encoders can produce evaluator-valid
  predictions after deterministic plumbing.
- **Engineering B — material:** a faithful capability claim needs a new model or
  learned method.
- **Operations ready:** artifacts are sufficient for a smoke run.
- **Operations gated:** media, agreement, license, storage, or compute needs a
  decision.
- **Operations blocked:** a necessary artifact is not presently obtainable.

An A/ready benchmark can be scientifically valid even if the baseline score is
poor. Benchmark compatibility is not a performance claim.

## Execution gates

### Gate 1: discovery complete

Each research lane has a candidate inventory with primary sources and no unresolved
basic questions about task, dataset, metrics, or availability.

### Gate 2: feasibility screened

For every serious candidate, confirm whether the dataset, official code, model
weights, and evaluation script are obtainable. Record licensing, storage, compute,
and environment blockers.

### Gate 3: benchmark suite selected

Choose the smallest suite that covers the implemented VidXP capabilities. Record
why each benchmark was selected and why close alternatives were rejected.

### Gate 4: adapter plan approved

Only now define the minimal code changes needed to emit the selected benchmark's
prediction format. Avoid building a generic framework before its requirements are
known.

### Gate 5: baselines executed

Run VidXP and the selected simple or published baselines using frozen configurations.
Preserve raw predictions, logs, timing records, dependency versions, hardware
details, and the evaluated Git commit.

### Gate 6: local evaluation justified

Create a local annotated corpus only for capabilities that remain uncovered after
the published-benchmark audit. The gap analysis must state why available public
benchmarks cannot answer the required evaluation question.

## First deliverable

The initial deliverable is one consolidated benchmark matrix covering all four
research lanes. It must make the following decisions easy:

1. Which benchmarks can we obtain and run?
2. Which require a small adapter?
3. Which are useful only for published context?
4. Which implemented VidXP capability has no suitable public benchmark?
5. What is the smallest credible benchmark suite to execute?

No implementation phase should begin until this matrix has been reviewed.

## Agent handoff format

Agents researching a lane should return:

1. A short lane conclusion.
2. Completed benchmark records for every serious candidate.
3. An explicit statement of what each result would and would not establish about
   VidXP.
4. A ranked shortlist with explicit verdicts.
5. Confirmed access, licensing, code, and compute blockers.
6. Unresolved questions requiring follow-up.
7. Primary-source links for every material claim.

Agents should not edit paper prose, invent a local dataset, implement adapters, or
report published scores as VidXP baselines during the discovery phase.

## Existing repository research

The existing [legacy benchmarking methodology](../benchmarking_research.md) is the starting
inventory and methodology reference. Its candidates and published values still need
to pass the access, reproducibility, and applicability checks defined here.

At the time of this brief, the project still used a single application module.
It was subsequently split into the CLI and shared core packages. The current
implementation boundary is documented in the
[benchmark-ready core contract](core_contract.md).

## Current research deliverables

- [Published benchmark catalog](benchmark_catalog.md) records the validated
  candidates, access constraints, fit decisions, and proposed execution order.
- [Execution readiness](execution_readiness.md) records the permitted implementation
  changes, exact adapter boundary, and revised executable shortlist.
- [Relevant research-paper inventory](research_papers.md) maps the papers
  to the benchmarks they introduce or use and provides the prioritized review
  queue.

These links were the discovery outputs available when this brief was written.
DiDeMo and HiREST were subsequently implemented and executed; their current
status is recorded in [results](results.md).
