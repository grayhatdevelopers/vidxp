# Codex evaluation with and without the VidXP integration

Collection index: [Benchmarking research](README.md)

Status: Development smoke recorded; held-out pilot not run

Last verified: 2026-09-02

This experiment measures whether the complete VidXP agent integration improves
a Codex agent's ability to find timestamped evidence in long videos. The
integration consists of the shipped video-evidence skill and the local stdio
MCP server. It is a product-level ablation, not a replacement for published
model benchmarks such as MAEB, MVEB, or AEGBench.

## What the comparison holds constant

Every task runs once in each condition with the same Codex model, reasoning
effort, prompt, media bytes, filesystem sandbox, network policy, output schema,
and fresh thread:

| Condition | VidXP access | Purpose |
| --- | --- | --- |
| `codex-vidxp` | The committed `vidxp-find-video-evidence` skill and local `vidxp-mcp` server | Measure the complete installed agent-plus-VidXP workflow |
| `codex-baseline` | No VidXP skill, MCP server, or direct VidXP CLI use | Measure what the same Codex agent can recover from local media without VidXP |

The conditions share an isolated `CODEX_HOME` that contains authentication but
no ambient MCP configuration. They use separate working directories so Codex's
repository skill discovery cannot leak the VidXP skill into the baseline. Setup
copies the exact committed skill into only the VidXP-on directory, and Promptfoo
passes the MCP definition only to the VidXP-on provider. Both directories expose
hard links to the same media bytes. Preflight compares the installed skill with
the committed source and rejects a VidXP skill in either the baseline or shared
parent workspace. Streaming traces record skill use and the complete MCP
trajectory. VidXP-off must use neither the skill nor VidXP through MCP or the
shell. VidXP-on may not fall back to FFmpeg or direct media inspection after
retrieval failure. Its response must preserve the source job and evidence IDs;
the scorer reopens the durable VidXP job and verifies that it was created
during the current trial, succeeded, matches the task query and media,
delivered ready evidence, and supports the returned intervals. Legitimate
discovery and polling choices are reported rather than forced into one exact
call sequence.

The committed configuration disables network access, persistent threads, result
caching, provider retries, parallel execution, and Codex subagents. These
controls reduce leakage, cross-task state, and accidental extra model runs.

## Why Promptfoo owns orchestration

[Promptfoo](https://www.promptfoo.dev/docs/providers/openai-codex-sdk/) runs the
paired provider matrix, repetitions, structured output, traces, usage
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
| Promptfoo Codex SDK | Selected: directly reuses Codex login, forwards per-provider Codex/MCP configuration, repeats paired cases, and captures usage and tool traces |
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
the VidXP-on workspace, initializes the system media runtime, opens Codex login
when authentication is absent, downloads and verifies the pinned LongVALE
archive, links the same five pilot videos into both condition workspaces,
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
skipped when all five videos and four modalities are already present. Setup
stops only its isolated local worker before applying the configuration; durable
jobs remain recoverable. The saved model-cache path is passed explicitly into
the benchmark's MCP process with model downloads disabled, so the process uses
the same prepared artifacts that setup verified. The benchmark pins the Codex
SDK directly and omits Promptfoo's unrelated optional provider packages from
the install.

## Validate before spending runs

Setup finishes by running preflight, which verifies the dedicated Codex
authentication, absence of ambient MCP configuration, skill isolation, all
five media files in both conditions, and the index paths. It then starts the
exact configured VidXP MCP process, checks required tools and prepared models,
and verifies that every pilot video is ready and indexed for all four
modalities. This makes a missing or incorrectly forwarded model cache fail
before a Codex run. To repeat the checks without setup, Codex inference, or
VidXP model inference, run:

```bash
./benchmarks/codex-mcp/run check
./benchmarks/codex-mcp/run preflight
```

The first paid/allowance-consuming smoke is one task in both conditions: two
Codex runs total.

```bash
./benchmarks/codex-mcp/run smoke
```

Inspect both outputs and their trajectories before continuing. This first pair
is development data: after any prompt, skill, tool, or scorer change, exclude it
from quality claims. The pilot command skips that pair and runs the remaining
nine tasks in two conditions with three repetitions: 54 Codex runs total.

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
MCP calls, shell calls, and the FFmpeg/ffprobe subset.

To inspect the durable VidXP result behind the latest comparison, including
the top fused interval and the best individual hit per modality, run:

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
The run count is therefore exactly two for the development smoke and 54 for the
held-out pilot.
Promptfoo reports usage, but it cannot determine the remaining ChatGPT-plan
allowance or convert subscription-authenticated runs into an exact dollar
charge; use the Codex account usage display for that limit.

The recorded development pair is summarized in
[Benchmark results](results.md#codex-mcp-development-smoke). It is retained to
diagnose the harness and current temporal behavior, not as held-out evidence.

## Scoring and interpretation

Each response must identify one interval. The deterministic scorer records
temporal IoU, R@1 at tIoU 0.3/0.5/0.7, interval validity, and whether the
expected VidXP boundary was respected. Promptfoo traces supply skill use, MCP
tool names, ordering, and inputs; because its Codex trace adapter does not
retain MCP result bodies, the scorer uses the returned source job ID to verify
the authoritative result directly in VidXP's durable job store. It also matches
each returned evidence ID, modality, and interval to ready evidence delivered
by that job. Report at least:

- success rate and mean IoU by condition;
- results by scene, action, sound, speech, and joint-modality task;
- input/cached/uncached/output/reasoning token usage, provider-estimated cost,
  latency, failures, and requests;
- skill and VidXP MCP tool trajectories for VidXP-on;
- indexing time, index size, model preparation, and machine details; and
- every excluded or failed task.

Do not call the nine-task held-out pilot a LongVALE result. A publishable result
requires the complete official evaluation split, its one-interval output
conversion, and the official evaluator. A centralized benchmark would
additionally need frozen agent versions, provider-independent authentication,
portable environments, and public result governance.

The VidXP-off condition is intentionally a local-agent baseline, not a native
video-model benchmark. The Codex SDK accepts text and local images but does not
accept video or audio inputs directly. With the network disabled and the
workspace read-only, VidXP-off may use installed read-only shell inspection
tools but cannot call VidXP or persist extracted media. Report this limitation
with the results; component-model quality remains covered by the published
benchmark record elsewhere in this collection.
