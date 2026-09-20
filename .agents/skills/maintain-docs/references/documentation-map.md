# VidXP documentation map

Use this map to select the smallest canonical owner. Recheck the repository
when paths or responsibilities have changed.

## Public and user-facing documents

| Document | Primary reader | Owns |
|---|---|---|
| `README.md` | Prospective and current users | Product purpose, supported outcomes, shortest successful start, and routes to detailed guides |
| `INSTALLATION_GUIDE.md` | End users | Choosing, installing, preparing, verifying, updating, and troubleshooting a local VidXP setup |
| `.github/release-intro.md` | People downloading a release | Release download choices and the shortest platform-specific start |
| `docs/local-api.md` | Application developers and advanced users | Connecting another local or hosted application through HTTP or MCP |
| `docs/integrations/openai-plugin.md` | Codex and ChatGPT users | Installing, connecting, using, and maintaining the OpenAI integration |
| `docs/deployment/coolify.md` | Operators | Deploying, securing, validating, backing up, and upgrading the Coolify stack |

Public documents describe outcomes and decisions in product language. Mention
provider, capability, process, or storage names only when the reader must act on
them.

## Contributor and maintainer documents

| Document | Primary reader | Owns |
|---|---|---|
| `docs/CONTRIBUTING.md` | Human contributors | Fork-based setup, repository orientation, development rules, validation, and pull-request submission |
| `docs/adding-a-capability.md` | Capability developers | The complete implementation path for adding a capability |
| `docs/desktop.md` | Desktop developers | Desktop ownership, project layout, local builds, validation, setup behavior, packaging, and signing |
| `docs/releasing.md` | Maintainers | Release channels, release automation, publication, verification, and recovery |
| `docs/architecture/platform.md` | System contributors and maintainers | Durable platform boundaries, contracts, invariants, and cross-surface behavior |
| `docs/deployment/gpu-evaluation.md` | Maintainers evaluating GPU support | Current decision, required boundaries, readiness gaps, validation, and blockers |
| `docs/benchmarking/README.md` and `docs/benchmarking/` | Researchers and benchmark contributors | Benchmark status, methods, evidence, results, and research history |

Contributor documentation must remain understandable to a person who has not
seen agent instructions or prior implementation discussions.

## Agent-facing instructions

| Document | Primary reader | Owns |
|---|---|---|
| `AGENTS.md` | Repository coding agents | Concise standing repository rules and validation expectations |
| `.agents/skills/` | Repository coding agents | Repeatable repository-maintenance workflows |
| `plugins/vidxp/skills/` | Agents using the installed VidXP product | Product operations such as installation, ingestion, and evidence discovery |

Do not move human contributor explanations into `AGENTS.md`. Do not put
repository-maintenance instructions in product-distributed skills.

## Evidence routes

Use these starting points, then follow the owning implementation:

| Subject | Evidence |
|---|---|
| Shared behavior and contracts | `src/vidxp/application.py`, `src/vidxp/control_plane.py`, `src/vidxp/application_models.py` |
| CLI behavior | `src/vidxp/cli_commands/` and CLI tests |
| Browser, HTTP, and MCP | `src/vidxp/frontend.py`, `src/vidxp/api_routes/`, `src/vidxp/mcp.py`, and protocol tests |
| Capabilities and models | `src/vidxp/capabilities/`, capability `requirements.txt` files, and model metadata |
| Desktop behavior and packaging | `desktop/src/`, `desktop/src-tauri/`, `desktop/package.json`, Desktop manifests, and `.github/workflows/desktop.yml` |
| Containers and deployment | `Dockerfile`, `compose.yaml`, `compose.coolify.yaml`, and container workflows |
| Releases | `.github/workflows/release-*.yml`, promotion and channel workflows, release configuration, and package metadata |
| Benchmarks | `src/vidxp/benchmarks/`, benchmark tests, and recorded evidence under `docs/benchmarking/` |

Treat another document as a navigation aid, not final evidence, when an owning
implementation or workflow exists.
