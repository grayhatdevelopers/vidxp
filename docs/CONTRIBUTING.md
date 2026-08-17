# Contributing to VidXP

Thanks for helping improve VidXP. This guide is for people contributing code,
documentation, tests, or product changes.

- For product installation and use, start with the
  [installation guide](../INSTALLATION_GUIDE.md).
- For release operations, use the [release process](releasing.md).
- For coding-agent instructions, use [`AGENTS.md`](../AGENTS.md).

## Before you start

- Open an issue before a large cross-capability or architecture change.
- Keep each pull request focused on one outcome and preserve unrelated work.
- State whether the change is user-visible or internal-only.
- Explain whether a storage or model change requires existing repositories to
  be rebuilt.
- Do not commit generated environments, model weights, media, indexes, local
  data, benchmark runs, or build outputs.

GPU support is deferred. Do not make CUDA the default or publish a GPU
installation path without a separately reviewed implementation and validation
plan.

## Set up a development environment

### 1. Fork and clone the repository

Fork the repository on GitHub, clone your fork, and add the main repository as
the `upstream` remote:

```bash
git clone https://github.com/YOUR-ACCOUNT/vidxp.git
cd vidxp
git remote add upstream https://github.com/grayhatdevelopers/vidxp.git
git fetch upstream
git switch -c my-change upstream/main
```

Your `origin` remote now points to your fork. The `upstream` remote lets you
bring later changes from the main repository into your branch.

### 2. Install contributor dependencies

Install the complete contributor environment:

```bash
uv sync --frozen \
  --extra local-worker \
  --extra frontend \
  --extra mcp \
  --extra server \
  --extra test \
  --extra benchmarks
```

### 3. Initialize and verify VidXP

Initialize and verify the local media runtime:

```bash
uv run --no-sync vidxp init
uv run --no-sync vidxp --version
uv run --no-sync vidxp doctor
uv run --no-sync pytest -q
```

`vidxp init` verifies FFmpeg, ffprobe, `libx264`, and `aac`. A fresh checkout
may report that model files have not been prepared; that is expected. Prepare
only the capability needed for manual testing:

```bash
uv run --no-sync vidxp prepare --modalities scene
uv run --no-sync vidxp doctor --modalities scene
```

Models and local repositories use the operating system's per-user VidXP data
directory unless `--data-dir` is explicitly set. They should not be written
into the checkout.

## Find the code you need

| Area | Main location |
|---|---|
| Product operations and contracts | `src/vidxp/application.py`, `control_plane.py`, and `application_models.py` |
| Capability models, indexing, and search | `src/vidxp/capabilities/` |
| CLI, browser, HTTP, and MCP adapters | `src/vidxp/cli_commands/`, `frontend.py`, `api_routes/`, and `mcp.py` |
| Jobs, storage, media, and infrastructure | `src/vidxp/core/`, `infrastructure/`, `ports.py`, and `workflow_*` |
| Desktop application | `desktop/` |
| Containers and deployment | `Dockerfile` and `compose*.yaml` |
| Tests and benchmarks | `tests/`, `src/vidxp/benchmarks/`, and `docs/benchmarking/` |
| Optional dependency groups | `src/vidxp/requirements/` and capability `requirements.txt` files |

## Work at the right boundary

VidXP exposes the same application behavior through CLI, browser, HTTP, MCP,
and Desktop surfaces.

- Put transport-neutral operations and result models in the application or
  control plane.
- Keep CLI, browser, HTTP, MCP, and Desktop code as thin adapters.
- Put capability-specific models, schemas, dependencies, indexing, and search
  logic under `src/vidxp/capabilities/<name>/`.
- Put filesystem, database, FFmpeg, execution, and process-supervision details
  behind the infrastructure and port boundaries.
- Keep benchmark-specific formats and evaluators under
  `src/vidxp/benchmarks/`.
- Keep server control processes model-free; model-provider work belongs in
  workers.

These boundaries protect several public guarantees:

- Commands and results expose stable identifiers and metadata, not storage
  keys or local filesystem paths.
- Long-running work crosses the durable job boundary so it can be monitored
  and recovered.
- Model downloads happen only after explicit preparation.
- Failed or cancelled generation leaves the previous active snapshot intact.
- Media, indexes, and artifacts become active only after their complete output
  has been written and validated.

This overview is enough for ordinary contributions. Changes to these ownership
boundaries require maintainer review. For a new capability, follow
[Adding a capability](adding-a-capability.md).

## Validate the change

Run the smallest relevant checks while developing, then the checks that cover
the changed boundary before opening a pull request.

| Change | Minimum validation |
|---|---|
| Python logic or contracts | Targeted test file, Ruff, then the full Python test suite |
| CLI | Help output, success path, failure path, and JSON output |
| Browser UI | Page render, form submission, job completion, and reload behavior |
| HTTP or MCP | Shared application tests plus the affected protocol contract |
| Models or media | Prepared and unprepared states plus a short real-media smoke test |
| Desktop | Frontend checks and the affected Tauri or packaging checks |
| Docker or Compose | Affected image smoke test and Compose configuration |
| Documentation | Commands, links, headings, terminology, and rendered tables |

Common checks are:

```bash
uv run --no-sync ruff check .
uv run --no-sync pytest -q
npm --prefix desktop run check
docker compose config --quiet
```

For documentation changes, run the repository's pinned Markdown linter and
local-link check:

```bash
npx --yes markdownlint-cli2@0.23.2
lychee "**/*.md" ".github/**/*.md" ".agents/**/*.md"
```

If Lychee is not installed, run the pinned container:

```bash
docker run --rm -v "$PWD:/input:ro" -w /input \
  lycheeverse/lychee:0.24.2 \
  "**/*.md" ".github/**/*.md" ".agents/**/*.md"
```

You can also let the documentation workflow run it on the pull request. The
check deliberately avoids network requests; do not treat it as proof that an
external website is currently available.

The Compose command above validates the local `compose.yaml`. The Coolify file
requires a complete deployment environment; follow its linked operator guide.

Run only the commands that apply to the change, but report every command you
actually ran. If you cannot complete a required check, explain why.

Call a test end-to-end only when it crosses the real boundary named in the
claim. State whether it exercised an actual process, provider, codec, database,
browser, or protocol instead of a mock.

For model-backed manual checks, use a short sample and only the affected
capability:

```bash
uv run --no-sync vidxp media import samplevideo.mp4 --json
uv run --no-sync vidxp index create <media-id> --modality scene
uv run --no-sync vidxp search scene "a person enters the room"
```

Desktop contributors should use the
[Desktop build instructions](desktop.md#build-locally). Coolify contributors
should use the [operator deployment guide](deployment/coolify.md).

## Open a pull request

Push your branch to your fork:

```bash
git push -u origin my-change
```

Then open a pull request from your fork's branch to
`grayhatdevelopers/vidxp:main` and use the repository's
[pull request template](../.github/pull_request_template.md). Keep it
concrete:

- **Related issue:** link an existing issue when the change has one.
- **Summary:** describe the outcome, affected interfaces or capabilities, and
  any compatibility or migration impact. Say explicitly when the change is
  internal-only.
- **Validation:** list exact commands and the real boundaries exercised. Do
  not replace results with “tests passed.”

Use a Conventional Commit title. Examples:

```text
feat(mcp): add clip artifact discovery
fix(frontend): keep search form submittable
docs: clarify local installation profiles
ci(release): verify signed macOS installers
```

The title determines the changelog and version impact when the pull request is
squash-merged:

| Change | Use | Version effect |
|---|---|---|
| New user-facing behavior | `feat` | Minor |
| User-facing correction | `fix` | Patch |
| Public documentation | `docs` | Patch |
| Performance, dependency, or revert entry | `perf`, `deps`, or `revert` | Patch |
| Internal maintenance with no release-note value | `chore`, `refactor`, `test`, `build`, `ci`, or `style` | None by itself |
| Breaking public contract | Add `!` and a `BREAKING CHANGE:` footer | Major |

A public fix implemented in CI or packaging is still a `fix`, not an internal
`ci` or `build` change. Maintainers should use the
[release process](releasing.md) for channel, candidate, and publication rules.

## Write product documentation for users

- Lead with what the user can do, then show the command or decision.
- Describe product features by outcome. Do not use provider names, internal
  capability names, or research terminology as product labels.
- Show an internal or CLI identifier only where the user must type it, and
  explain it in product language.
- Keep the README product-focused and the installation guide procedural.
- Put deployment internals under `docs/deployment/` and maintainer procedures
  in their dedicated guides.
- Mark previews, deferred work, and unsupported setups explicitly.
- Never publish a command sequence that skips a required installation,
  initialization, model preparation, or activation step.

## Get help

- Open an [issue](https://github.com/grayhatdevelopers/vidxp/issues) for a bug
  or scoped proposal.
- Use [Discord](https://grayhat.studio/discord) for contributor discussion.
