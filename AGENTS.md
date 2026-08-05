# Contributor agent guide

VidXP is a Python application with CLI, HTTP, MCP, and Desktop surfaces that
share the same application contracts. Read
[`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md) before making changes.

## Working in the repository

- Keep pull requests focused on one outcome and preserve unrelated work.
- Put shared behavior in the application or control plane; keep CLI, HTTP,
  MCP, and Desktop code as thin adapters.
- Keep capability-specific models, schemas, dependencies, indexing, and search
  logic under `src/vidxp/capabilities/`.
- Do not commit generated environments, model weights, media, indexes, build
  outputs, or local data.
- Follow the Conventional Commit and release-note rules in the contributing
  guide. State explicitly when a change is internal-only.

## Validation

Run the smallest relevant checks while developing, then the applicable checks
from the contributing guide before submitting a pull request. Common checks:

```bash
uv run --no-sync ruff check .
uv run --no-sync pytest -q
npm --prefix desktop run check
```

Do not describe mocked tests as end-to-end validation. Report the exact
commands run and any required validation that could not be completed.
