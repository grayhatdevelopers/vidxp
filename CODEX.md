# Codex contributor notes

Start with [`AGENTS.md`](AGENTS.md) and
[`docs/CONTRIBUTING.md`](docs/CONTRIBUTING.md).

1. Inspect the affected implementation, shared contracts, and existing tests.
2. Make the smallest coherent change at the correct architecture boundary.
3. Reuse repository tooling and generated-file workflows instead of manually
   recreating derived artifacts.
4. Run validation appropriate to the changed surface.
5. Summarize the outcome, exact validation performed, and remaining risks.

Ask before introducing a new dependency, migration, public contract change, or
architecture direction that is not already established by the repository.
