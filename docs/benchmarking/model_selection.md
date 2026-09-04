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
- VideoPrism ranks fixed multi-frame clips by global text-video similarity; and
- reciprocal rank fusion groups overlapping results into coarse candidate
  moments while preserving their source records.

This modular path remains the product control. No current evidence requires
replacing every provider or moving to a single trained temporal model.

VideoPrism's published action results do not validate this fixed-window
ranking as temporal action localization. A direct conformance check found that
the pinned Transformers port matches Google's official Flax checkpoint; the
remaining action failure is therefore in the product's global-similarity
ranking design, not the converted model weights.

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

FineLAP supports separating the global and local outputs. It does not establish
VidXP's global top-three gate followed by one pooled activation ranking over
those windows. Section 3.3 trains local scores against short event phrases and
frame labels inside a clip; the paper's Limitations section explicitly leaves
long-form audio and temporally enhanced audio-text retrieval unevaluated. The
VidXP selector failed all four held-out tasks at final top-three target coverage
and is rejected. Existing indexes remain usable for a FineLAP control because
they already label both representations; a replacement provider requires a new
sound index.

The matching replacement task is audio moment retrieval: a full natural-language
query and a long audio sequence go in, and ranked start/end intervals come out.
[DCASE 2026 Task 6](https://dcase.community/challenge2026/task-audio-moment-retrieval-from-long-audio-results)
provides the current direct evidence. Its official MS-CLAP/QD-DETR baseline
scored 13.56 R1@0.7 on the hidden evaluation, while a 211.87M-parameter
M2D-CLAP/CG-DETR system scored 48.59. The winning system's code and checkpoint
were not verified as public, so it is the architecture and quality target rather
than an immediately adoptable provider.

The released compatibility fallback is CASTELLA-trained UVCOM through
[Lighthouse](https://github.com/line/lighthouse). It predicts intervals from a
one-second audio-feature sequence, has an official checkpoint, documents CPU
inference, and supports 300-second audio. Its published CASTELLA R1@0.7 is 20.3
and the paper identifies sub-ten-second moments as a weakness. Test that provider
in isolation before changing the default or rebuilding indexes. DASM, FlexSED,
WSTAG, and PE-A-Frame remain separate short-event or event-phrase comparators.

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

No replacement boundary model has been selected. The overlapping-action
control did produce a near-target shorter record, but the existing union joined
it to its neighbors. A held-out follow-up then tested a simple coarse-to-fine
path without union. Fine candidate recall improved, but the coarse gate and
similarity ranking missed most answers, so that path is not a product fix.
Point-to-Span and shot-proposal fusion also remain concluded benchmark controls.
Their exact results and deviations are recorded in the
[research adoption record](research_adoption.md).

## Next action correction

Do not tune fusion, window overlap, or query wording again for this failure.
The held-out comparison already showed that useful fine windows exist but raw
VideoPrism similarity ranks most of them too low.

The replacement boundary is now explicit: keep VidXP's action API and reusable
index, but replace global clip ranking with a trained temporal grounder that
consumes a sequence of visual features and predicts intervals. An et al.,
[HieraMamba](https://openaccess.thecvf.com/content/CVPR2026/html/An_HieraMamba_Video_Temporal_Grounding_via_Hierarchical_Anchor-Mamba_Pooling_CVPR_2026_paper.html),
CVPR 2026, establishes the long-video multi-scale grounding design. An, Jain,
and Grauman, [UniversalVTG](https://arxiv.org/abs/2604.08522), 2026, adds one
cross-domain checkpoint and is the closest technical product candidate.

Neither release can be adopted unchanged: both depend on a CUDA-oriented Mamba
stack, and the checked repositories do not provide a top-level product license.
The next implementation task is therefore a bounded compatibility decision:
confirm a lawful checkpoint and a CPU or Apple-Silicon runtime for that exact
grounder. If either requirement fails, reject it and evaluate the Apache-2.0
Lighthouse CPU path as the fallback, recording its 150-second input limit. Do
not change product ranking until one candidate passes that gate on the frozen
action tasks.
