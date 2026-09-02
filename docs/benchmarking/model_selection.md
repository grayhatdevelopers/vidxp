# Evidence retrieval direction

Collection index: [Benchmarking research](README.md)

Status: Current product and evaluation decision

Last verified: 2026-09-03

The [research adoption record](research_adoption.md) is the source of truth for
paper-derived product behavior. The [paper inventory](research_papers.md)
records relevant work without implying that VidXP adopts it.

## Product target

VidXP gives an AI agent a compact, inspectable view of a video library: matching
speech, sounds, frames, action clips, timestamps, and playable evidence. The
agent remains responsible for interpreting that evidence and answering the
user. VidXP does not need to replace the agent with one all-in-one video model.

A product-level comparison succeeds when VidXP preserves or improves the
agent's grounded answer while reducing the media and text the agent must
inspect. Report answer correctness and evidence support together with input,
cached-input, output, and reasoning tokens; elapsed time and estimated cost;
tool calls; and retrieval or timestamp metrics. Temporal IoU diagnoses interval
quality, but it is not the product's only outcome.

## Current product path

VidXP builds reusable local indexes for separate evidence types:

- faster-whisper and Qwen3 Embedding produce timestamped speech evidence;
- FineLAP retrieves environmental-sound clips;
- SigLIP 2 retrieves sampled visual frames;
- VideoPrism retrieves multi-frame action clips; and
- reciprocal rank fusion groups overlapping results into coarse candidate
  moments while preserving their source records.

This modular path remains the product control. No current evidence requires
replacing every provider or moving to a single trained temporal model.

An optional small language model may plan searches or summarize retrieved
evidence. That is a VidXP product option, not a paper-derived requirement. It
must be compared with the deterministic path on answer quality, tokens,
latency, cost, and fallback behavior before becoming a default.

## Confirmed limits and decisions

### Keep FineLAP's retrieval outputs separate

Xiquan Li et al., [“FineLAP: Taming Heterogeneous Supervision for Fine-grained
Language-Audio Pretraining”](https://aclanthology.org/2026.acl-long.473/), ACL
2026, Sections 3.2–3.3, trains separate global and local audio projections for
clip-level and frame-level supervision. VidXP previously stored both outputs in
one collection and ranked the raw records together.

That integration was invalid: the two score lists did not form one calibrated
ranking. On four held-out sound tasks, the mixed top three contained target
evidence on 0/4 tasks; querying the representations separately did so on 3/4.

Standard sound search now uses two separate stages. Global ten-second clips
select candidate regions, then dense activations are ranked only against other
dense activations inside those regions. The returned timestamps come from the
activation, while its metadata identifies the parent clip for inspection. If a
selected clip has no activation records, search returns the clip instead of
hiding available evidence.

FineLAP supports separating the global and local outputs. The two-stage
long-video orchestration, candidate depth, context metadata, and fallback are
original VidXP engineering rather than claims from the paper. Existing sound
indexes do not need rebuilding.

### Treat fused intervals as evidence envelopes

The current fusion groups overlapping records, scores each group with
reciprocal rank fusion, and returns its earliest start and latest end. The RRF
formula and `k = 60` come from Gordon Cormack, Charles Clarke, and Stefan
Buettcher, [“Reciprocal Rank Fusion Outperforms Condorcet and Individual Rank
Learning Methods”](https://doi.org/10.1145/1571941.1572114), SIGIR 2009. The
temporal grouping and interval union are VidXP controls; that paper does not
define them.

On the development query, relevant evidence ranked first but an eight-second
action record widened a six-second reference to `0–8.0075` seconds. A separate
eight-task control also showed that adding modality ranks can overrule a strong
single-modality result. Therefore the fused interval is a coarse evidence
envelope, not a claim of an exact event boundary. The agent should inspect the
contained records or delivered clip before making a precise statement.

No replacement boundary model has been selected. Point-to-Span, overlapping
action windows, and shot-proposal fusion remain concluded benchmark controls,
not product behavior. Their exact results and deviations are recorded in the
[research adoption record](research_adoption.md).

## Next product check

Do not add another model or temporal rule for the current correction. After the
two-stage sound search is committed, rerun the existing paired Codex smoke only
with maintainer approval. Compare the same answer and evidence fields, temporal
metrics, token categories, elapsed time, estimated cost, and tool-call counts.

Use that result to answer two concrete questions:

1. Does the agent receive relevant, inspectable sound evidence without the
   mixed FineLAP ranking?
2. Does VidXP reach a similarly grounded conclusion with less agent work than
   direct video inspection?

Only a measured remaining failure should open a new model or localization
decision. Candidate papers stay in the inventory until that decision exists.
