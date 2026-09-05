# Codex evaluation with and without the VidXP integration

Collection index: [Benchmarking research](README.md)

Status: Development smoke recorded; held-out pilot not run

Last verified: 2026-09-06

This experiment measures whether the complete VidXP agent integration improves
a Codex agent's ability to find timestamped evidence in long videos. The
integration consists of the shipped video-evidence skill and the local stdio
MCP server. It is a product-level ablation, not a replacement for published
model benchmarks such as MAEB, MVEB, or AEGBench.

The primary product question is whether the agent returns a practical clip that
contains the event while using fewer tokens. Exact temporal IoU remains a
secondary boundary-quality measurement; it is not discarded or presented as
the serving objective.

## What the comparison holds constant

Each repetition uses the same Codex model, reasoning effort, task, media bytes,
filesystem sandbox, network policy, output schema, and fresh thread:

| Condition | VidXP access | Purpose |
| --- | --- | --- |
| `codex-vidxp` | The committed `vidxp-find-video-evidence` skill and local `vidxp-mcp` server | Measure the complete installed agent-plus-VidXP workflow |
| `codex-baseline` | No VidXP skill, MCP server, or direct VidXP CLI use; other local tools are unrestricted | Measure what the same Codex agent does without VidXP |
| `codex-model-only` | No VidXP, shell, image viewer, browser, computer-use tool, or discovered skill | Measure the same model without developer or MCP tooling |

The conditions share an isolated `CODEX_HOME` that contains authentication but
no ambient MCP configuration. Separate working directories prevent repository
skill discovery from leaking VidXP into the baselines. Setup installs the exact
committed skill only in the VidXP directory, and Promptfoo passes the MCP
definition only to that provider. All three directories expose hard links
to the same media bytes. Preflight verifies those links and rejects a VidXP skill
in the baseline or shared parent directory.

The scorer enforces capability boundaries, not an agent script. The baseline
cannot call VidXP but may use any other available local tool. The model-only
condition exposes neither VidXP nor local agent tools. The VidXP condition
cannot inspect media directly through the agent shell, but the MCP server may
use VidXP's configured FFmpeg runtime internally. Loading the skill or following
one discovery sequence is not required; the agent must submit a matching MCP
retrieval and return evidence from its durable result. Every generated case receives one opaque
retrieval nonce. The scorer requires that nonce as the job's idempotency key,
which keeps repeated tasks fresh without relying on an agent-created name.
Skill use, polling choices, FFmpeg use, and every tool call remain reported.

The model-only condition is a tool-free model control, not a native video-model
benchmark. The Codex SDK does not attach the MP4 as model input, so this lane
measures what the model returns without a media access path.

The committed configuration disables network access, persistent threads, result
caching, provider retries, parallel execution, and Codex subagents. These
controls reduce leakage, cross-task state, and accidental extra model runs.

## Why Promptfoo owns orchestration

[Promptfoo](https://www.promptfoo.dev/docs/providers/openai-codex-sdk/) runs the
three-condition provider matrix, repetitions, structured output, traces, usage
collection, and local reports. VidXP's Python benchmark code owns task expansion
and deterministic scoring. This division avoids rebuilding a general evaluation
runner while keeping official temporal metrics and dataset logic reviewable in
the repository. This follows OpenAI's documented
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
setup verifies FFmpeg and ffprobe and, when they are absent, installs them
through a supported package manager. On a fresh macOS machine, install Homebrew
and `node@22` before running setup. Setup can then install FFmpeg automatically
when needed.

From the repository root, run the automated setup:

```bash
./benchmarks/codex-mcp/run setup
```

The command installs the pinned Python and Node dependencies, creates isolated
state outside the checkout, installs the committed VidXP evidence skill only in
the VidXP workspace, initializes the system media runtime, opens Codex login
when authentication is absent, downloads and verifies the pinned LongVALE
archive, links the same five pilot videos into all three condition workspaces,
prepares the four required capabilities, indexes the media, saves the evaluation
environment in the ignored `benchmarks/codex-mcp/.env` file, and runs preflight.
Accept the LongVALE dataset terms before running it. Do not copy or commit the
generated `auth.json`.

By default, mutable state goes under the operating system's user data
directory. Set only `VIDXP_EVAL_ROOT` when it needs to live elsewhere:

```powershell
$env:VIDXP_EVAL_ROOT = 'D:\vidxp-eval'
npm --prefix benchmarks/codex-mcp run setup
```

The setup is safe to rerun. Cached downloads and prepared models are reused,
including VidXP Desktop's existing model cache when it is present. Set
`VIDXP_MODEL_CACHE` before setup to select another prepared cache. Indexing is
stored in a directory named for the `INDEX_SCHEMA_VERSION` read from VidXP, so
a schema change rebuilds derived benchmark data without deleting the preceding
index. Indexing is skipped when all five videos and four modalities are already
present. Setup stops only its isolated local worker before applying the
configuration; durable jobs remain recoverable. The saved model-cache path is
passed explicitly into
the benchmark's MCP process with model downloads disabled, so the process uses
the same prepared artifacts that setup verified. The benchmark pins the Codex
SDK directly and omits Promptfoo's unrelated optional provider packages from
the install.

## Validate before spending runs

Setup finishes by running preflight, which verifies the dedicated Codex
authentication, absence of ambient MCP configuration, skill isolation, all
five media files in all three conditions, and the index paths. It then starts the
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
nine tasks in three conditions with three repetitions: 81 Codex runs total.
Condition order rotates across repetitions so serial timing does not always put
the same condition first or last.

```bash
./benchmarks/codex-mcp/run pilot
```

Both commands finish with a comparison of pass counts, temporal IoU, recall at
each IoU threshold, boundary errors, elapsed time, token usage, estimated cost,
skill loading, and MCP or direct-media tool calls. Token reporting separates
total input, cached input, uncached input, output, and reasoning tokens. Reasoning
is included in output. The provider estimate may charge cached and uncached
input differently, so total-token ordering does not have to match estimated-cost
ordering.

Print the latest saved comparison again, without inference, with:

```bash
./benchmarks/codex-mcp/run results
```

Add `--all` to include every per-run interval in a full pilot report. Add
`--responses` to print each final answer, returned modalities, source job, and
evidence count. The report also shows total agent items, all tool calls, VidXP
MCP calls, shell calls, and the FFmpeg/ffprobe subset. For VidXP runs, it
also reads each saved job and reports fused retrieval R@1, R@3, and R@5, the top
fused interval, its constituent hits, and the best retained hit per modality.
This exposes what fusion actually used and which fused rank retained each hit;
it does not rerun retrieval.
Candidates removed by the current pre-fusion or final `top_k` cannot be
reconstructed from the saved job, and the report states that limitation. Use
`--no-retrieval` only when the saved VidXP jobs are unavailable.

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

Promptfoo Community and the repository's Python evaluation code are no-cost
open-source software. The local MCP server and local VidXP processing create no
OpenAI or Anthropic inference charge, but downloading and indexing consume local
bandwidth, disk, electricity, and any paid infrastructure the operator chooses;
the dataset and model licenses still apply. Codex inference authenticated
through the dedicated ChatGPT login consumes the account's Codex plan allowance
or credits. API-key authentication instead incurs API usage charges. No
LLM-as-judge assertion is enabled, so this scaffold does not add grader calls.
The run count is therefore exactly three for the development smoke and 81 for
the held-out pilot.
Promptfoo reports usage, but it cannot determine the remaining ChatGPT-plan
allowance or convert subscription-authenticated runs into an exact dollar
charge; use the Codex account usage display for that limit.

The recorded development runs are summarized in
[Benchmark results](results.md#codex-mcp-development-smoke). It is retained to
diagnose the harness and current temporal behavior, not as held-out evidence.

## Scoring and interpretation

Each task asks for one event and one evidence clip, so this harness measures
evidence-backed retrieval rather than general video question answering. The
prompt targets a 10-second clip and accepts 8–12 seconds. A bounded chunk hit
requires the clip to cover at least half of the annotated event that can fit in
10 seconds. This lets a normal fixed window containing a short event pass while
rejecting both a two-second blink and a whole-video answer. The 10-second target
is a VidXP product-evaluation policy, not a metric taken from LongVALE.

The deterministic scorer also retains temporal IoU, R@1 at tIoU 0.3/0.5/0.7,
start/end/duration error, interval validity, and whether the expected VidXP
boundary was respected. Promptfoo traces supply skill use, MCP
tool names, ordering, and inputs; because its Codex trace adapter does not
retain MCP result bodies, the scorer uses the returned source job ID to verify
the authoritative result directly in VidXP's durable job store. It also matches
each returned evidence ID, modality, and interval to ready evidence delivered
by that job. Report at least:

- bounded-chunk hit rate and mean event coverage by condition;
- mean IoU and R@1 at tIoU 0.3/0.5/0.7 as secondary boundary diagnostics;
- results by scene, action, sound, speech, and joint-modality task;
- input/cached/uncached/output/reasoning token usage, provider-estimated cost,
  latency, failures, and requests;
- skill and VidXP MCP tool trajectories for VidXP-on;
- indexing time, index size, model preparation, and machine details; and
- every excluded or failed task.

The report never applies the product gate to a development smoke. For the pilot,
the high-level gate passes only when VidXP matches or improves the local-tool
baseline's bounded-chunk hit rate and uses fewer total tokens. The model-only
condition is supporting evidence, not part of that gate. Latency, cost, calls,
boundary quality, and all three raw condition summaries remain visible; the
single verdict does not replace them. Exact-boundary underperformance is a
documented research limitation, not grounds to fail a useful fixed-window
retrieval result.

Two recorded development pairs predate this contract and used the old
exact-interval prompt. Keep their raw IoU, token, and trace measurements, but do
not report them as bounded-chunk product-gate results. Evaluation
`eval-2uz-2026-09-05T17:39:13` uses the bounded-clip contract but predates the
third condition; it remains a two-condition smoke rather than a product gate.

Do not call the nine-task held-out pilot a LongVALE result. A publishable result
requires the complete official evaluation split, its one-interval output
conversion, and the official evaluator. A centralized benchmark would
additionally need frozen agent versions, provider-independent authentication,
portable environments, and public result governance.

The VidXP-off condition is intentionally the same local agent without VidXP. It
is not required to use FFmpeg, inspect a particular artifact, or follow a
prescribed call sequence. The model-only condition removes the Codex local-tool
surface as well. Neither is a native video-model benchmark because the Codex SDK
does not pass the MP4 directly to the model. Component-model quality remains
covered by the published benchmark record elsewhere in this collection.
