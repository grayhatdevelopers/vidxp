# Contributing to VidXP

Thanks for helping improve VidXP. Contributions may target the local product,
public interfaces, model capabilities, deployment, documentation, or
benchmarks.

## Before you start

- Open an issue before a large cross-capability or architecture change.
- Keep pull requests focused on one user-visible outcome.
- Preserve the shared application contracts: CLI, UI, HTTP, and MCP should not
  grow separate implementations of the same operation.
- State whether a storage/model change requires existing repositories to be
  rebuilt.
- GPU support is deferred. Do not silently make CUDA the default or add a new
  GPU installation path without the documented validation gates.

## Development setup

### 1. Clone and install the complete contributor environment

```bash
git clone https://github.com/grayhatdevelopers/vidxp.git
cd vidxp
uv sync --frozen \
  --extra local-worker \
  --extra frontend \
  --extra mcp \
  --extra server \
  --extra test \
  --extra benchmarks
```

The repository lock selects CPU PyTorch on Linux and Windows.

### 2. Initialize the media runtime

```bash
uv run --no-sync vidxp init
```

This verifies FFmpeg, ffprobe, `libx264`, and `aac`. It may offer an explicit,
confirmed system package-manager command; Python dependency installation never
changes system packages.

### 3. Verify the environment

```bash
uv run --no-sync vidxp --version
uv run --no-sync vidxp doctor
uv run --no-sync pytest -q
```

`doctor` may report that model artifacts have not been prepared; that is
expected in a fresh contributor environment. Dependency, import, FFmpeg, or
codec failures are not expected. Prepare only the modality needed for manual
testing:

```bash
uv run --no-sync vidxp prepare --modalities scene
uv run --no-sync vidxp doctor --modalities scene
```

Models and normal local repositories use the operating system’s per-user VidXP
data directory. They are not written into the checkout unless you explicitly
override `--data-dir`.

## Project map

- Product operations and contracts live in `application.py`,
  `control_plane.py`, `application_models.py`, and the media, artifact, and
  query services.
- Capability-specific indexing and retrieval live in `capabilities/`.
- CLI, Streamlit, HTTP, and MCP adapters live in `cli.py`, `cli_commands/`,
  `frontend.py`, `api.py`, `api_routes/`, and `mcp.py`.
- Durable execution and storage live in `job_service.py`, `workflow_*`,
  `execution.py`, `core/`, `infrastructure/`, and `ports.py`.
- Deployment and desktop packaging live in `Dockerfile`, `compose*.yaml`, and
  `desktop/`.
- Tests are under `tests/`; benchmark adapters and documentation are under
  `src/vidxp/benchmarks/` and `docs/benchmarking/`.
- Optional dependency groups are under `src/vidxp/requirements/`.

Generated environments, model weights, media, indexes, artifacts,
`benchmark_runs/`, build outputs, and local data do not belong in commits.

## Architecture rules

### Put logic at the correct boundary

- Capability-specific models, indexing, retrieval, schemas, and dependencies:
  `src/vidxp/capabilities/<name>/`.
- Transport-neutral operations and result models: the application/control
  plane.
- CLI, Streamlit, HTTP, and MCP: thin input/output adapters.
- Filesystem, Chroma, PostgreSQL, DBOS, FFmpeg, and process supervision:
  infrastructure/ports.
- Benchmark-specific data formats and evaluator behavior: benchmarks only.

### Preserve public contracts

- Public commands/results use stable IDs and metadata, not storage keys or
  local filesystem paths.
- Local and server adapters call the same typed application operations.
- Long-running work crosses the durable job boundary.
- Model downloads happen only through explicit preparation.
- Media and artifact publication remains atomic.
- A failed or cancelled generation must not replace the previous active
  snapshot.
- Server control processes remain model-free; provider work belongs in workers.
- Local defaults remain under the per-user VidXP data root, not the current
  working directory.

### Adding or changing a capability

Follow [Adding a capability](adding-a-capability.md). At minimum:

- declare the definition and typed operation schemas;
- keep provider dependencies in that capability’s `requirements.txt`;
- pin model identity, revision, checksum, license metadata, and disclosed size;
- wire the package extra in `pyproject.toml`;
- test provider readiness without downloading models; and
- document whether old indexes must be rebuilt.

## Validation

Run the smallest relevant checks while iterating, then the complete applicable
gate before opening a pull request.

| Change | Minimum validation |
|---|---|
| Python logic or contracts | Targeted pytest file + full `pytest -q` |
| CLI | Command help, success path, failure path, JSON output |
| Streamlit | Full-page render, form submit, job progress/terminal state, reload |
| HTTP API | Shared application test + route/auth/error contract |
| MCP | Tool contract + `vidxp-mcp --check` |
| Media/artifacts | Real ffprobe/FFmpeg smoke + path/ID confinement |
| Models/providers | Dependency check, prepared/unprepared states, short real-media smoke |
| Desktop | Frontend typecheck/lint/tests/build, Rust tests, setup/close lifecycle |
| Docker/Compose | Dockerfile targets, `docker compose config`, health checks |
| Documentation | Commands, relative links, headings, and rendered tables |

Common commands:

```bash
uv run --no-sync ruff check .
uv run --no-sync pytest -q
npm --prefix desktop run check
docker compose config --quiet
```

Pull-request CI treats Markdown and the documentation directory as
documentation-only. Tests and Desktop-only changes run the code suite without
building a container; product, tooling, workflow, and unknown new paths default
to the code suite plus container validation. Desktop and CodeQL triggers use
directory or language globs rather than enumerating individual source files.

Desktop validation requires the pinned uv sidecar before Rust tests:

```bash
npm --prefix desktop ci
npm --prefix desktop run model-catalog:check
npm --prefix desktop run notices:check
npm --prefix desktop run check
npm --prefix desktop run sidecar:windows
cargo test --locked --manifest-path desktop/src-tauri/Cargo.toml
```

Install the pinned notice generator and prefetch the locked Cargo graph before
running notice generation in offline/frozen mode:

```bash
cargo install cargo-about --version 0.9.1 --locked --features cli
cargo fetch --manifest-path desktop/src-tauri/Cargo.toml --locked
npm --prefix desktop run notices:write
npm --prefix desktop run notices:check
```

The Desktop model-cache catalog is derived from the canonical Python
capability/model contracts. Do not edit its generated entries by hand:

```bash
npm --prefix desktop run model-catalog:write
npm --prefix desktop run model-catalog:check
```

Use `npm --prefix desktop run sidecar:unix` on macOS or Linux. Validate
`compose.coolify.yaml` only with a complete deployment test environment; its
required image, secret, hostname, and upload variables intentionally make a
bare `docker compose config` fail.

Model-backed manual smoke tests should use a short sample and only the
modalities affected by the change:

```bash
uv run --no-sync vidxp media import samplevideo.mp4 --json
uv run --no-sync vidxp index create <media-id> --modality scene
uv run --no-sync vidxp search scene "a person enters the room"
```

Do not describe mocked adapter tests as end-to-end validation. If a test does
not exercise a real process, provider, codec, database, browser, or protocol
boundary, say so.

## Pull requests

The repository's
[pull request template](../.github/pull_request_template.md) is the required
starting point. GitHub loads it automatically for new pull requests. Complete
all three sections:

- **Summary:** describe the user-facing outcome and important compatibility or
  migration behavior.
- **Validation:** list the exact commands and real boundaries exercised. Do not
  replace results with “tests passed.”
- **Changelog:** add the correct fragment, or explain why the change is
  dependency-only/internal and should receive `skip-changelog`.

Keep the description compact, but include:

- what users can do after the change;
- why the change is needed;
- important design or compatibility decisions;
- new dependencies, environment variables, downloads, or migrations;
- validation actually performed; and
- screenshots or sample output for user-interface changes.

Use Conventional Commit prefixes for commits, for example:

```text
feat(mcp): add clip artifact discovery
fix(frontend): keep search form submittable
docs: clarify local installation profiles
```

## Release notes and versions

Release Please derives versions and changelog entries from Conventional
Commits. Make the squash-merge commit or the commits retained by a regular
merge describe the public change accurately:

- `feat` creates a feature release.
- `fix` and `perf` create a patch release.
- `!` or a `BREAKING CHANGE:` footer creates a breaking release.
- `docs`, `test`, `ci`, `build`, `refactor`, and `chore` do not publish by
  themselves.

Write user-visible `feat`, `fix`, and `perf` subjects for users, not for the
implementation history. Internal corrections to an unreleased feature should
remain part of that feature rather than appear as fictional public bug fixes.
Release Please prepares the combined version and changelog in a pull request;
do not edit released changelog sections by hand.

## Documentation style

- Lead with the outcome, then the command or decision.
- Prefer short sections, bullets, and comparison tables over dense paragraphs.
- Keep the README product-focused and the installation guide procedural.
- Put deployment internals in `docs/deployment/`.
- Mark previews, deferred work, and unsupported topologies explicitly.
- Never publish a command sequence that skips a required install,
  initialization, preparation, or activation step.

## Getting help

- Open an [issue](https://github.com/grayhatdevelopers/vidxp/issues) for a bug
  or scoped proposal.
- Use [Discord](https://grayhat.studio/discord) for contributor discussion.
- Maintainers should follow the [release process](releasing.md).
