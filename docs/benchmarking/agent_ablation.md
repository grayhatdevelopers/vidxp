# Codex evaluation with and without VidXP MCP

Collection index: [Benchmarking research](README.md)

Status: Runnable scaffold; no agent results recorded

Last verified: 2026-09-01

This experiment measures whether access to VidXP through its local stdio MCP
server improves a Codex agent's ability to find timestamped evidence in long
videos. It is a product-level ablation, not a replacement for published model
benchmarks such as MAEB, MVEB, or AEGBench.

## What the comparison holds constant

Every task runs once in each condition with the same Codex model, reasoning
effort, prompt, media workspace, filesystem sandbox, network policy, output
schema, and fresh thread:

| Condition | VidXP access | Purpose |
| --- | --- | --- |
| `codex-vidxp-mcp` | The local `vidxp-mcp` stdio server | Measure the complete agent-plus-VidXP workflow |
| `codex-no-mcp` | No MCP server and no direct VidXP CLI use | Measure what the same Codex agent can recover from the local media without VidXP |

The two conditions use an isolated `CODEX_HOME` that contains authentication but
no ambient MCP servers, plugins, or skills. Promptfoo receives the MCP definition
through the Codex provider's `cli_config`; the MCP-off provider receives no such
definition. Streaming traces must prove that MCP-on used at least one VidXP tool
and MCP-off neither used a VidXP tool nor invoked the VidXP CLI through the shell.

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

Only these five videos are indexed for the first ten-task pilot:

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

Promptfoo 0.122.2 requires Node.js 22.22.0 or newer. The benchmark-local
`.npmrc` enforces that requirement so an unsupported runtime fails during
installation instead of failing after Codex runs have begun. You also need
`uv` and the Codex CLI on `PATH`. The setup verifies FFmpeg and ffprobe and,
when they are absent, installs them through a supported package manager. On a
fresh macOS machine, install Homebrew before running setup so VidXP can install
FFmpeg automatically.

From the repository root, run the automated setup:

```powershell
npm --prefix benchmarks/codex-mcp run setup
```

The command installs the pinned Python and Node dependencies, creates isolated
state outside the checkout, initializes the system media runtime, opens Codex
login when authentication is absent, downloads and verifies the pinned
LongVALE archive, copies the five pilot videos, prepares the four required
capabilities, indexes the media, saves the evaluation environment in the
ignored `benchmarks/codex-mcp/.env` file, and runs preflight. Accept the
LongVALE dataset terms before running it. Do not copy or commit the generated
`auth.json`.

By default, mutable state goes under the operating system's user data
directory. Set only `VIDXP_EVAL_ROOT` when it needs to live elsewhere:

```powershell
$env:VIDXP_EVAL_ROOT = 'D:\vidxp-eval'
npm --prefix benchmarks/codex-mcp run setup
```

The setup is safe to rerun. Cached downloads and prepared models are reused,
and indexing is skipped when all five videos and four modalities are already
present. The benchmark pins the Codex SDK directly and omits Promptfoo's
unrelated optional provider packages from the install.

## Validate before spending runs

Setup finishes by running preflight, which verifies the dedicated Codex
authentication, absence of ambient MCP configuration, all five media files,
the index paths, and a real VidXP MCP handshake. To repeat the configuration and
preflight checks without setup or Codex inference, run:

```powershell
npm --prefix benchmarks/codex-mcp run check
npm --prefix benchmarks/codex-mcp run preflight
```

The first paid/allowance-consuming smoke is one task in both conditions: two
Codex runs total.

```powershell
npm --prefix benchmarks/codex-mcp run eval:smoke
```

Inspect both outputs and their trajectories before continuing. The pilot command
runs ten tasks in two conditions with three repetitions: 60 Codex runs total.

```powershell
npm --prefix benchmarks/codex-mcp run eval:pilot
```

Promptfoo Community and the repository's Python evaluation code are no-cost
open-source software. The local MCP server and local VidXP processing create no
OpenAI or Anthropic inference charge, but downloading and indexing consume local
bandwidth, disk, electricity, and any paid infrastructure the operator chooses;
the dataset and model licenses still apply. Codex inference authenticated
through the dedicated ChatGPT login consumes the account's Codex plan allowance
or credits. API-key authentication instead incurs API usage charges. No
LLM-as-judge assertion is enabled, so this scaffold does not add grader calls.
The run count is therefore exactly two for the smoke and 60 for the pilot.
Promptfoo reports usage, but it cannot determine the remaining ChatGPT-plan
allowance or convert subscription-authenticated runs into an exact dollar
charge; use the Codex account usage display for that limit.

## Scoring and interpretation

Each response must identify one interval. The deterministic scorer records
temporal IoU, R@1 at tIoU 0.3/0.5/0.7, interval validity, and whether the expected
MCP boundary was respected. Report at least:

- success rate and mean IoU by condition;
- results by scene, action, sound, speech, and joint-modality task;
- token usage, latency, failures, and retries;
- VidXP MCP tool trajectories for MCP-on;
- indexing time, index size, model preparation, and machine details; and
- every excluded or failed task.

Do not call the ten-task pilot a LongVALE result. A publishable result requires
the complete official evaluation split, its one-interval output conversion, and
the official evaluator. A centralized benchmark would additionally need frozen
agent versions, provider-independent authentication, portable environments, and
public result governance.

The MCP-off condition is intentionally a local-agent baseline, not a native
video-model benchmark. The Codex SDK accepts text and local images but does not
accept video or audio inputs directly. With the network disabled and the
workspace read-only, MCP-off may use installed read-only shell inspection tools
but cannot call VidXP or persist extracted media. Report this limitation with
the results; component-model quality remains covered by the published benchmark
record elsewhere in this collection.
