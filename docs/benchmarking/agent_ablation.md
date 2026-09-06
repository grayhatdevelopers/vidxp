# Codex evaluation with and without the VidXP integration

Collection index: [Benchmarking research](README.md)

Status: Isolated held-out pilot scored; product gate failed

Last verified: 2026-09-06

This experiment measures whether the complete VidXP agent integration improves
a Codex agent's ability to find timestamped evidence in long videos. The
integration consists of the shipped video-evidence skill and the local stdio
MCP server. It is a product-level ablation, not a replacement for published
model benchmarks such as MAEB, MVEB, or AEGBench.

The primary product question is whether the agent returns the event within a
small ranked set of practical clips while using fewer tokens. Exact temporal
IoU and top-one ordering remain secondary measurements; neither is discarded
or presented as the entire serving objective.

## What the comparison holds constant

Each repetition uses the same Codex model, reasoning effort, user prompt, task,
source-video identity, output schema, and fresh thread. The direct-local and
clean-user workspaces receive hard links to the same bytes. VidXP indexes those
bytes before timing, then its agent workspace omits the relative source path so
ordinary shell inspection fails.

| Condition | VidXP access | Purpose |
| --- | --- | --- |
| `codex-vidxp` | The committed `vidxp-find-video-evidence` skill and local `vidxp-mcp` server | Measure the complete installed agent-plus-VidXP workflow |
| `codex-baseline` | No VidXP skill, MCP server, or direct VidXP CLI use; system commands plus the host FFmpeg and ffprobe installation are available | Measure what the same Codex agent does without VidXP |
| `codex-clean-user` | Writable terminal and network, but an initial PATH containing only operating-system commands; no VidXP skill or MCP | Measure what a non-developer setup can bootstrap without inheriting the host's Homebrew or repository tools |

Each condition has a separate `CODEX_HOME` and working directory. Setup copies
only authentication from a common isolated login home; it does not share
configuration, sessions, or discovered skills. It installs the committed skill
only in the VidXP workspace and passes the MCP definition only to that provider.
Before every condition run, a Promptfoo hook clears prior outputs and installed
tools from that condition's workspace. It retains fixed media for the two
non-VidXP conditions and only the committed skill for VidXP. This makes
repetitions independent instead of
letting a previous agent's files or clean-user bootstrap affect the next run.
Preflight checks both hard links, rejects any VidXP-on source path, rejects
ambient MCP configuration and leaked VidXP skills, and verifies that the
clean-user login shell cannot initially resolve
`ffmpeg`, `ffprobe`, `vidxp`, or `vidxp-mcp`.
Each condition uses an
[OpenAI-documented Codex permission profile](https://developers.openai.com/codex/permissions)
that denies filesystem-root access, reopens only Codex's minimal runtime paths
and its own writable workspace, and sets network access for that condition.
The direct-local profile also reads the installation prefix containing FFmpeg
and ffprobe. The clean-user and VidXP profiles cannot execute those host
binaries, even by absolute path. On macOS, before any model call, preflight runs
the pinned Codex sandbox and verifies the denied host read, allowed workspace
read and write, and expected FFmpeg access for all three conditions. A command
that tries a blocked host path is not a breach. The scorer separately rejects
actual VidXP use in non-VidXP conditions and direct source-media inspection in
the VidXP condition.

The scorer enforces capability boundaries, not an agent script. The direct-local
baseline cannot call VidXP but may use system commands, FFmpeg, and ffprobe. The
clean-user condition retains its terminal and network and may install tools into
its own workspace; Homebrew and the repository environment are absent from its
initial PATH. The VidXP condition
cannot inspect media directly through the agent shell, but the MCP server may
use VidXP's configured FFmpeg runtime internally. Loading the skill or following
one discovery sequence is not required; the agent must submit a matching MCP
retrieval and return evidence from its fresh durable result. The user prompt
never names VidXP, FFmpeg, a condition, or a required call sequence. Skill use,
polling choices, model turns, and Promptfoo-recorded items and tool calls remain
reported.

The VidXP and direct-local permission profiles disable network access. The
clean-user profile enables unrestricted command-line network access so the
agent can bootstrap tools. Every lane disables persistent
threads, result caching, provider retries, parallel execution, and Codex
subagents.

The timed comparison starts after setup: all five videos are already indexed
for scene, action, sound, and speech in VidXP. Download, model preparation,
media import, and indexing are excluded from all three agent times and measured
separately below.

## Why Promptfoo owns orchestration

[Promptfoo](https://www.promptfoo.dev/docs/providers/openai-codex-sdk/) runs the
three-condition Codex matrix and the separate local-SLM condition. It owns task
execution, repetitions, saved results, usage, assertions, and reports. VidXP's
Python benchmark code expands the frozen tasks, scores them deterministically,
and adapts the local Pydantic-AI/Ollama agent through Promptfoo's documented
[Python provider contract](https://www.promptfoo.dev/docs/providers/python/).
The adapter does not select tasks, repeat runs, score results, aggregate metrics,
or write a separate result format. This follows OpenAI's documented
[Codex evaluation workflow](https://learn.chatgpt.com/use-cases/ai-app-evals).

Promptfoo is not needed to choose a component model from published leaderboards.
It is used here because this experiment evaluates an agent workflow and its tool
trajectory. Inspect AI or Harbor would become stronger candidates if the work
expands into a provider-independent public agent benchmark or centralized
leaderboard.

The harness choice was made against the actual subscription-authenticated Codex
constraint, not just against generic eval feature lists:

| Harness | Decision for this experiment |
| --- | --- |
| Promptfoo Codex SDK | Selected: directly reuses Codex login, forwards each condition's Codex/MCP configuration, repeats cases, and captures usage and tool traces |
| Native Codex SDK/CLI | Capable, but would require custom pairing, retry, aggregation, and report plumbing that Promptfoo already provides |
| [Inspect AI](https://inspect.aisi.org.uk/) | Stronger for portable research evals, but subscription-authenticated Codex requires a custom bridge rather than its standard model path |
| [EvalBench](https://github.com/GoogleCloudPlatform/evalbench) | Supports MCP scenarios, but its documented Codex path is API-key oriented and its simulated-user turns would add runs not needed here |
| [Harbor](https://github.com/harbor-framework/harbor) | Strong containerized agent benchmark infrastructure, but heavyweight and credential/API oriented for this local pilot |
| [DeepEval](https://github.com/confident-ai/deepeval) | Potential later scorer layer; it does not provide the direct Codex runner needed here |
| Braintrust, LangSmith, or Phoenix | Potential result/trace backends, not substitutes for the local Codex runner |

If this grows into the centralized public benchmark discussed in the roadmap,
revisit Inspect or Harbor. That is a different deliverable from establishing the
VidXP MCP effect under the user's existing Codex plan.

## Pilot dataset and exact videos

The first pilot uses the human-refined LongVALE evaluation annotations and the
smallest raw evaluation archive, `LongVALE_test_1171_part_9.zip`. At pinned
dataset revision `18889b01886e30c36b0d1c650ac4439ad460ee73`, the archive is
1,063,510,782 bytes, has SHA-256
`c83d62557f102c6d41ea95c2c3b3581657481c8646cc70b1e12a85ead27a7ae3`, and
contains 28 videos. The annotation file is 4,522,592 bytes.

Only these five videos are indexed for the ten-task development and pilot set:

| Video ID | Seed coverage |
| --- | --- |
| `ZYTmgi1pAIE` | rain, wind, engine start, bell, and a visual title transition |
| `ZIdFAGJrlCw` | driving action, siren, engine revving, and a short sketching action |
| `ZGXCr5n8Frg` | visible speaker plus spoken corporate content |
| `_py1WXVX4oc` | sign-language action, title text, and a ringing telephone |
| `ZVUAC3m48G0` | short cooking actions and a visual-plus-drumbeat event |

The task manifest is
[`benchmarks/codex-mcp/tasks/longvale-part9-pilot.json`](../../benchmarks/codex-mcp/tasks/longvale-part9-pilot.json).
It contains scene, action, environmental-sound, and speech cases, including
events that require more than one channel. The full LongVALE denominator remains
the later official target; this deliberately selected pilot validates the
integration and cannot support a LongVALE quality claim.

OVSD remains a separate, open-licensed scene-boundary regression source. It can
test segmentation and temporal-unit construction, but it has no natural-language
retrieval, action-label, environmental-sound, speech, or cross-modal task. OVSD
therefore does not replace LongVALE in this ablation.

## Prepare the isolated environment

Promptfoo 0.122.2 requires Node.js 22.22.0 or newer. On macOS and Linux, the
benchmark runner selects a compatible Node installation automatically,
including Homebrew's versioned Node 22 installation. You do not need to change
`PATH` in each terminal. You also need `uv` and the Codex CLI on `PATH`. The
setup verifies FFmpeg and ffprobe for VidXP and, when they are absent, installs them
through a supported package manager. On a fresh macOS machine, install Homebrew
and `node@22` before running setup. Setup can then install FFmpeg automatically
when needed.

From the repository root, run the automated setup:

```bash
./benchmarks/codex-mcp/run setup --machine-id mac-m2-01
```

The command installs the pinned Python and Node dependencies, creates isolated
state and separate condition homes outside the checkout, installs the committed
VidXP evidence skill only in the VidXP workspace, initializes the system media
runtime, opens Codex login
when authentication is absent, downloads and verifies the pinned LongVALE
archive, links the same five pilot videos into the two non-VidXP workspaces,
prepares the four required capabilities, indexes the media, saves the evaluation
environment in the ignored `benchmarks/codex-mcp/.env` file, and runs preflight.
Accept the LongVALE dataset terms before running it. Do not copy or commit the
generated `auth.json`. `--machine-id` selects the stable ID defined in the
[metric database](metric_database.md#machines-used); it is stored in `.env`, so
rerunning setup does not require an export or another flag. Add a new machine
to that table before assigning it a new ID, and replace `mac-m2-01` in the
example when running elsewhere.

By default, mutable state goes under the operating system's user data
directory. Set only `VIDXP_EVAL_ROOT` when it needs to live elsewhere:

```powershell
$env:VIDXP_EVAL_ROOT = 'D:\vidxp-eval'
npm --prefix benchmarks/codex-mcp run setup -- --machine-id win-hp-01
```

The setup is safe to rerun. Cached downloads and prepared models are reused,
including VidXP Desktop's existing model cache when it is present. Set
`VIDXP_MODEL_CACHE` before setup to select another prepared cache. Indexing is
stored in a directory named for the `INDEX_SCHEMA_VERSION` read from VidXP, so
a schema change rebuilds derived benchmark data without deleting the preceding
index. Indexing is skipped when all five videos and four modalities are already
present. Setup stops every VidXP worker still using its isolated benchmark
state before applying the configuration; durable jobs remain recoverable. The
saved model-cache path is passed explicitly into the benchmark's MCP process
with model downloads disabled, so the process uses the same prepared artifacts
that setup verified. The benchmark pins the Codex SDK directly and omits
Promptfoo's unrelated optional provider packages from the install.

### Measure indexing separately

The agent ablation intentionally starts from an existing index. Measure its
offline cost with three fresh, isolated index builds:

```bash
./benchmarks/codex-mcp/run indexing
```

Pass another positive repetition count only when needed. This command reuses
the prepared pinned model cache with downloads disabled, rotates video order,
and records import and four-modality indexing time per video, whole-run time,
real-time factor, index bytes, and aggregate statistics. It removes only its
own temporary data and index directories; it does not modify the prepared index
used by the agent runs. The path-free JSON result is written under
`docs/benchmarking/runs/` for review, then linked from the
[metric database](metric_database.md#offline-indexing-measurements). It can take
substantially longer than the agent smoke because it rebuilds every modality
for all five videos in every repetition.

## Validate before spending runs

Setup finishes by running preflight, which verifies the dedicated Codex
authentication, separate condition homes, absence of ambient MCP configuration,
skill and clean-PATH isolation, all
five media files in the direct-local and clean-user conditions, their exact
durations, the absent VidXP-on source paths, the repository machine ID, and the
index paths. It then starts the
exact configured VidXP MCP process, checks required tools and prepared models,
and verifies that every pilot video is ready and indexed for all four
modalities. This makes a missing or incorrectly forwarded model cache fail
before a Codex run. To repeat the checks without setup, Codex inference, or
VidXP model inference, run:

```bash
./benchmarks/codex-mcp/run check
./benchmarks/codex-mcp/run preflight
```

The first paid/allowance-consuming smoke is one task in all three conditions:
three Codex runs total.

```bash
./benchmarks/codex-mcp/run smoke
```

Inspect all outputs and their trajectories before continuing. This first set
is development data: after any prompt, skill, tool, or scorer change, exclude it
from quality claims. The pilot command skips that task and runs the remaining
three repetitions by default: nine tasks × three conditions × three repetitions,
or 81 Codex runs total.
Condition order rotates across repetitions so serial timing does not always put
the same condition first or last. Repetition is already part of this one
evaluation command; do not invoke it three times manually. The report aggregates
all repetitions by condition and prints the per-run rows with `--all`.

```bash
./benchmarks/codex-mcp/run pilot
```

Pass a positive repetition count when a larger variance sample is worth the
additional time and Codex allowance. For example, five repetitions are one
135-run evaluation, not five manual pilot invocations:

```bash
./benchmarks/codex-mcp/run pilot 5
```

After the matched pilot is frozen, a VidXP-only intervention may be run without
spending another direct-local or clean-user control:

```bash
./benchmarks/codex-mcp/run vidxp
```

This runs the same nine held-out tasks and three repetitions, but only the
`codex-vidxp` condition. It is intended for a declared follow-up intervention,
not a replacement paired pilot. Compare it with the frozen controls by run ID
and report that the condition was measured later rather than counterbalanced in
the same evaluation.

The separate local-agent lane gives the shipped VidXP skill and five required
MCP tools to the approved self-hosted Ollama model:

```bash
./benchmarks/codex-mcp/run slm
```

It uses the same prompt, output schema, scorer, prepared index, and evidence
attestation as VidXP-on, while sending model requests only to the loopback
runtime. Promptfoo runs the nine held-out tasks three times by default, stores
the detailed run in its normal local database, and includes it in `run results`,
`run view`, and `run export`. Before Promptfoo creates an evaluation, preflight
builds the exact structured-output agent, discovers its five allowed MCP tools,
and closes the MCP session. That wiring check uses Pydantic AI's test model, so
it makes neither a local-model request nor an MCP tool call. The separate MCP
preflight verifies the prepared models and five indexed videos. First run
`uv run --no-sync vidxp local-answers prepare --yes`. The benchmark starts the
saved local runtime when needed and stops only the process it started. It does
not download or substitute a model. The first provider call includes a managed
runtime cold start when Ollama was not already running; later calls in that
Promptfoo worker reuse it. The agent has no terminal or file access; its callable
surface is the five allowlisted VidXP MCP tools. The neutral prompt's media-path
field remains present for parity but cannot be opened by this provider.

All benchmark MCP processes explicitly disable VidXP's optional internal query
model. This prevents `query_video` from making hidden model calls after a local
runtime has been installed. In the local-SLM condition, the metered local model
is the agent; VidXP remains the evidence backend.

The provider records Ollama input/output tokens, local model requests, MCP calls,
and latency in Promptfoo's response. Provider charge and external-agent calls
are zero; memory, energy, and local compute cost are unmeasured. The agent reuses
VidXP's product request settings, including `reasoning_effort: none`; this uses
Qwen 3.5's direct-response mode instead of spending the output allowance on an
unreturned reasoning trace. The limits live in `promptfooconfig.yaml`: 12 model
requests, 10 tool calls, 2,048 output tokens per request, a 180-second model-call
timeout, and a 300-second Promptfoo per-task timeout. The last value is not a
video-duration limit. Limit failures remain failed Promptfoo cases rather than
being retried by a separate runner.

Both commands finish with a comparison of pass counts, temporal IoU, recall at
each IoU threshold, boundary errors, elapsed time, average and total token usage
and cost,
model turns, skill loading, and Promptfoo-recorded MCP and shell tool calls.
Token reporting separates
total input, cached input, uncached input, output, and reasoning tokens. Reasoning
is included in output. The report preserves Promptfoo's supplied cost unchanged.
The harness pins Promptfoo
[0.122.2](https://www.npmjs.com/package/promptfoo?activeTab=versions), the npm
`latest` release when rechecked on September 6, 2026. Its embedded
`gpt-5.6-sol` rates are $5 per million uncached input tokens, $0.50 per million
cached input tokens, and $30 per million output tokens. Promptfoo applies its
own long-context rule to the aggregate usage returned by the Codex SDK. This
dollar value is a consistent benchmark metric, not an end-user price, API
invoice, or measured Codex-plan charge.

Print the latest saved comparison again, without inference, with:

```bash
./benchmarks/codex-mcp/run results
```

Add `--all` to include every per-run interval in a full pilot report. Add
`--responses` to print each final answer, returned modalities, source job, and
evidence count. The report also shows agent runs, model turns, total recorded
items, model requests, all tool calls, VidXP MCP calls, and shell calls. Counts
come from the items saved in each Promptfoo provider response; the report does
not infer tool use from command text. Codex supplies its recorded items, and the
local provider records its actual MCP calls and local request count. For VidXP
runs, it
also reads each saved job and reports fused retrieval R@1, R@3, and R@5, the top
fused interval, its constituent hits, and the best retained hit per modality.
This exposes what fusion actually used and which fused rank retained each hit;
it does not rerun retrieval.
Candidates removed by the current pre-fusion or final `top_k` cannot be
reconstructed from the saved job, and the report states that limitation. Use
`--no-retrieval` only when the saved VidXP jobs are unavailable.

After a scorer or media-duration correction, add `--rescore` to re-audit saved
responses and traces against the current scorer and validated task durations,
without calling Codex or another model. This requires the original VidXP
durable jobs and labels the output as a current deterministic audit; it does
not overwrite the at-run Promptfoo scores in the retained export.

The `trace` command remains as an explicit alias for inspecting the same saved
retrieval details:

```bash
./benchmarks/codex-mcp/run trace
```

This reads saved jobs only. It reports each boundary and its IoU against the
task annotation without starting Codex, invoking a model, or rerunning the
benchmark.

Before comparing localization methods, export all indexed records and scores
for the task's declared action, scene, sound, and speech modalities:

```bash
./benchmarks/codex-mcp/run probe TASK_ID --top-k 3
```

Unlike `trace`, this command performs one local text-embedding inference per
declared modality and queries every indexed record for that video. The output
keeps each modality's raw distance, rank, interval, representation metadata,
model identity, runtime, and call count separate; it does not pretend the
scores are calibrated across models. It writes JSON under the ignored
benchmark state directory. The report includes the reconstructed current
fusion and IoU/boundary errors plus top-retrieved and best-individual-record
IoU per modality, and prints those measurements directly after the run. The
best-individual value is a diagnostic oracle, not a production prediction. The
command does not invoke Codex or change production search.

After all ten probes exist, replay their saved rankings at independent
pre-fusion depths while holding the final result depth at ten:

```bash
./benchmarks/codex-mcp/run depth
```

This makes no model, Codex, or API calls. It prints R@1, evidence-board R@3,
R@5, and R@10 curves and saves the per-task candidates beside the probes. The
tested depths are diagnostic samples, not proposed defaults. The control shows
whether candidate collection loses a match and whether fusion keeps separate
moments bounded.

Compare a saved probe with the benchmark-only Point-to-Span ASG adaptation:

```bash
./benchmarks/codex-mcp/run compare TASK_ID
```

This performs no model calls. It prints the control and adapted interval, IoU,
boundary errors, runtime, and per-modality candidate counts, then saves the
full method record beside the probe. The saved development result concluded
this diagnostic: it improved the coarse union but used only the sound curve and
remained below direct media inspection. Do not run the held-out agent batch for
this adaptation alone.

Build and compare one isolated overlapping VideoPrism action representation:

```bash
./benchmarks/codex-mcp/run representation TASK_ID \
  --sample-fps 4 \
  --stride-samples 8
```

VideoPrism always receives 16 sampled frames. This example therefore produces
nominal four-second windows every two seconds. The four-second size is a fixed-
window control evaluated by Point-to-Span; the 50% overlap is a VidXP experiment
setting, not a parameter copied from that paper. The command requires both
values, builds a separate action-only index, reuses the saved scene, sound, and
speech probe, and reports action retrieval, fused IoU, indexing time, index
bytes, record count, and query time. It makes no Codex calls, but it does run
VideoPrism indexing and one action text embedding. Confirm before running it.

After every held-out action task has a saved probe, compare the current,
fine-only, and coarse-to-fine action paths with:

```bash
./benchmarks/codex-mcp/run representation --held-out \
  --sample-fps 4 \
  --stride-samples 8
```

The coarse-to-fine control keeps a four-second record when its midpoint lies
inside any top-three eight-second result. It preserves the fine similarity
order and returns one record without union. The report includes top-1 IoU,
top-three and full-list candidate recall, per-task intervals and ranks, index
cost, and model-call counts. It makes no Codex or API calls.

Reproduce the concluded disjoint shot-proposal control:

```bash
./benchmarks/codex-mcp/run shots TASK_ID
```

This implements the no-postprocessing ShotDetect path from Diwan et al. with
their published PySceneDetect content threshold `53`. It runs PySceneDetect
`0.7` through OpenCV, reuses the saved 1 fps SigLIP2 curve, and ranks each shot
by its best contained scene score. A separate VidXP-only result applies the
existing RRF score to each fixed shot using the best overlapping top-three rank
from every other saved modality; those hits can change the proposal rank but
cannot expand its boundary. The command reports both results, oracle proposal
IoU, recall thresholds, proposal count, and detection time. It makes no model
calls and writes no index. The paper used CLIP-ViT-B/32 and sampled within each
shot; the report records both VidXP adaptations and excludes SimpleWatershed.

After exporting fresh probes for tasks 3–10, aggregate the held-out local
comparison:

```bash
./benchmarks/codex-mcp/run shots --held-out
```

The command preserves the manifest's declared modalities. It compares scene
ranking with proposal-preserving RRF only where scene is declared, reports the
two action-and-sound tasks separately, counts evidence that overlaps multiple
proposals, and states whether each reference crosses a detected boundary. Shot
detection makes no model calls; the required probes for this pilot make 16
local text-embedding calls in total. This is not a Promptfoo or Codex run.

Compare the saved full-query rankings with a frozen manual wording ceiling and
FineLAP's clip/frame paths:

```bash
./benchmarks/codex-mcp/run queries
```

This command uses the eight saved held-out probes as the baseline, makes 32
local text-embedding calls, and writes one ignored JSON report. It does not run
Codex or Promptfoo. The manual phrases use only content stated in the task query
and are not an automatic planner result. For sound, the report separately ranks
FineLAP's whole-window and dense-activation records; it does not invent a final
merge rule.

Check the historical FineLAP selector on the four held-out sound tasks:

```bash
./benchmarks/codex-mcp/run sound
```

This runs the exact full-query product path, then one local diagnostic pass per
task to report global-gate coverage, final activation coverage and rank, IoU,
boundary errors, time, and model/vector-call counts. It makes no Codex,
Promptfoo, or API call.

Open the saved local results in Promptfoo's browser interface without running
another evaluation:

```bash
./benchmarks/codex-mcp/run view
```

The viewer opens `http://localhost:15500` and continues running until you press
`Ctrl-C`.

### Preserve a reviewed run

Promptfoo's local database contains the full interactive run, but it is not
portable or committed. After reviewing a run, preserve its latest evaluation
with:

```bash
./benchmarks/codex-mcp/run export
```

Pass one or more evaluation IDs after `export` to preserve older runs. The
command uses Promptfoo's native JSON export, removes Codex raw response bodies,
session IDs, secrets, and personal paths, and writes an importable artifact to
`docs/benchmarking/runs/`. It retains the prompt and provider configuration,
final responses, scores, usage, and traces; the compact local-SLM MCP item list
is retained because it is that provider's auditable tool record. Import one into
a separate Promptfoo database with
`npm --prefix benchmarks/codex-mcp run promptfoo -- import <artifact> --new-id`
when the full UI is needed.

Every newly generated test row records `VIDXP_EVAL_MACHINE_ID`, and the export
wrapper repeats that stable ID at `metadata.vidxpExport.machineId`. Machine
hardware and software are defined once in the metric database instead of copied
into every large Promptfoo artifact.

Promptfoo Community and the repository's Python evaluation code are no-cost
open-source software. The local MCP server and local VidXP processing create no
OpenAI or Anthropic inference charge, but downloading and indexing consume local
bandwidth, disk, electricity, and any paid infrastructure the operator chooses;
the dataset and model licenses still apply. Codex inference authenticated
through the dedicated ChatGPT login consumes the account's Codex plan allowance
or credits; the dollar column is Promptfoo's provider estimate for comparison,
not a measured plan charge or invoice. If a run uses API-key authentication,
actual charges must come from the provider's billing records. No
LLM-as-judge assertion is enabled, so this scaffold does not add grader calls.
The run count is therefore exactly three for the development smoke and 81 for
the default held-out pilot; an explicit repetition override changes only the
pilot count.
Promptfoo reports usage, but it cannot determine the remaining ChatGPT-plan
allowance or convert subscription-authenticated runs into an exact dollar
charge; use the Codex account usage display for that limit.

The recorded development runs are summarized in
[Benchmark results](results.md#codex-mcp-development-smoke). It is retained to
diagnose the harness and current temporal behavior, not as held-out evidence.

## Scoring and interpretation

Each task asks for one event and up to three distinct candidate clips, ordered
most to least likely. Each clip targets 10 seconds and accepts 8–12 seconds.
Success@3 requires at least one clip to cover half of the annotated event that
can fit in 10 seconds. This lets a fixed window containing a short event pass
while rejecting two-second blinks, whole-video answers, and unbounded result
lists. Returning fewer than three candidates is valid. The window and result
limit are VidXP product-evaluation policies, not LongVALE metrics.
The scorer rejects exact duplicate intervals but does not impose an arbitrary
overlap threshold because legitimate windows can overlap the same event.
VidXP candidates share one retrieval job; the agent is not required to launch
more searches or inspect every artifact to fill the list.

The deterministic scorer retains Success@1, reciprocal rank, candidate count,
top-one and best-of-three temporal IoU, R@1 and R@3 at tIoU 0.3/0.5/0.7,
start/end/duration error for the first candidate, interval validity, and whether
the expected VidXP boundary was respected. Promptfoo traces supply skill use, MCP
tool names, ordering, and inputs; because its Codex trace adapter does not
retain MCP result bodies, the scorer uses the returned source job ID to verify
the authoritative result directly in VidXP's durable job store. It also matches
each candidate's evidence IDs and modalities to ready evidence from that job,
then verifies that its interval overlaps the delivered evidence range.

The report also scores VidXP's visible evidence separately from the agent's
answer. MCP surfaced-target Hit@1 and Hit@3 ask whether a ready evidence tile
shown by `get_job_evidence` covers the same half-event threshold. These retrieval
diagnostics do not require an 8–12-second final clip and do not enter the paired
product gate. They distinguish “VidXP found and exposed it” from “the agent
selected and returned it.”

Attestation requires only the evidence IDs because the durable job already owns
their intervals and metadata. The agent may use the initial board, metadata,
keyframes, or clips and inspect an artifact only when that resolves a mismatch
or uncertainty. Any extra inspection still counts toward time, tokens, and tool
calls.

Report at least:

- bounded-chunk Success@3, Success@1, reciprocal rank, and candidate count;
- VidXP MCP surfaced-target Hit@1 and Hit@3;
- top-one and best-of-three IoU plus R@1/R@3 at tIoU 0.3/0.5/0.7;
- results by scene, action, sound, speech, and joint-modality task;
- input/cached/uncached/output/reasoning token usage, Promptfoo-supplied
  comparison cost, latency, failures, agent runs, and model turns;
- skill and VidXP MCP tool trajectories for VidXP-on;
- indexing time, index size, model preparation, and machine details; and
- every excluded or failed task.

The scorer binds each durable result to the query the agent actually submitted;
it does not require a verbatim copy of the user's wording. Inspecting a clip
delivered by that job remains VidXP use, while opening the source media directly
is a condition violation.

The report never applies the product gate to a development smoke. For the pilot,
every matched VidXP/direct-local pair must first be condition-valid and
scorable. Otherwise the gate is not scored and any valid-pair comparison is
diagnostic only. With complete pairs, the gate passes only when VidXP matches
or improves bounded-chunk Success@3 and uses fewer total tokens. The clean-user
condition is supporting evidence. Latency, cost, calls, boundary quality, and
all three raw summaries remain visible; the verdict does not replace them.

Evaluation
[`eval-7VR-2026-09-06T10:58:07`](runs/eval-7VR-2026-09-06T10-58-07.json)
completed the isolated held-out pilot in all three conditions. The current
deterministic scorer accepts all 81 saved runs. The agent-level gate failed,
while VidXP's visible top three evidence tiles surfaced the target on 19/27
VidXP runs. See [Benchmark results](results.md#current-codex-mcp-held-out-pilot)
for the measurements and interpretation.

The fixed prompt already asks for up to three grounded candidates and says not
to reconfirm evidence that already supports one. The shipped skill now makes
the handoff precise: for a requested shortlist, preserve each distinct ready
candidate from the initial ranked evidence up to three, dropping only failures,
duplicates, or evidence-confirmed mismatches. It does not require opening or
parsing every artifact. In the saved run, five final misses had a qualifying
visible top-three tile and seven did not. The `vidxp` command measures this
declared handoff intervention against frozen controls without rerunning them.
That comparison can support an intervention analysis, but it must not be called
the original counterbalanced product gate.

Selected earlier runs remain as diagnostics, not product-gate evidence. The
exact-interval runs preserve the failure and later boundary behavior;
`eval-2uz-2026-09-05T17:39:13` is a bounded two-condition smoke; and
`eval-YDK-2026-09-05T20:29:45` established that a tool-free third lane cannot
inspect the media. Their artifacts and valid conclusions are linked from the
[metric database](metric_database.md#historical-agent-runs).

Do not call the nine-task held-out pilot a LongVALE result. A publishable result
requires the complete official evaluation split, its one-interval output
conversion, and the official evaluator. A centralized benchmark would
additionally need frozen agent versions, provider-independent authentication,
portable environments, and public result governance.

The VidXP-off condition is intentionally the same local agent without VidXP. It
is not required to use FFmpeg, inspect a particular artifact, or follow a
prescribed call sequence. The clean-user condition instead begins without
third-party host executables but may obtain its own tools. It measures bootstrap
behavior, not a native video model. Component-model quality remains covered by
the published benchmark record elsewhere in this collection.
