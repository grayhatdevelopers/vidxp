---
name: maintain-docs
description: Maintain and review VidXP repository documentation, including README.md, INSTALLATION_GUIDE.md, docs/**/*.md, release copy, and contributor guides. Use when documentation or product behavior, CLI, HTTP, MCP, Desktop, deployment, releases, contributor workflows, architecture, research, or integrations change. Route information to the correct human or agent audience, verify claims against repository evidence, preserve nonredundant details, improve prose and flow, and report validation and warnings. Do not use for product-distributed VidXP skill authoring unless repository documentation also changes.
---

# Maintain VidXP documentation

Keep VidXP documentation accurate, appropriately placed, and readable without
requiring the reader to know the implementation first.

## Load the project context

1. Read the applicable `AGENTS.md` instructions and `docs/CONTRIBUTING.md`.
2. Read [references/documentation-map.md](references/documentation-map.md) to
   identify the document owner and audience.
3. Read [references/writing-standard.md](references/writing-standard.md) before
   creating, rewriting, or reviewing human-facing prose.
4. Inspect `git status` and the relevant diff. Preserve unrelated work and do
   not stage or commit changes unless the user asks.
5. Run the established Markdown checks before editing to record the baseline:

   ```bash
   npx --yes markdownlint-cli2@0.23.2
   lychee "**/*.md" ".github/**/*.md" ".agents/**/*.md"
   ```

   If Lychee is unavailable locally, use the pinned container command in
   `docs/CONTRIBUTING.md` or report that the link check was not run. Record the
   baseline instead of attributing pre-existing findings to the current change.

## Maintain the documentation

### 1. Define the change

- Identify the behavior, decision, workflow, or correction being documented.
- Determine whether the task concerns one document, the current diff, or a
  wider documentation audit.
- Identify every affected surface: end user, integrator, operator, contributor,
  maintainer, researcher, or agent.

Do not treat an audience label as proof that prose is suitable for that
audience. Evaluate the document's vocabulary, sequence, assumptions, and level
of detail.

### 2. Choose the owner

- Update the smallest document that owns the information.
- Link to the canonical explanation instead of copying it into several files.
- Use tutorial, how-to, reference, and explanation as reader-need categories,
  not as a mandatory directory structure.
- Keep human contributor guidance separate from agent instructions.
- Keep repository-maintenance guidance separate from the product-distributed
  skills under `plugins/vidxp/skills/`.
- Do not create a documentation changelog, decision log, incident report, or
  new directory taxonomy unless the repository has adopted it or the user asks.

### 3. Establish evidence

- Verify claims against current code, configuration, tests, workflows, package
  metadata, or release automation.
- Prefer the implementation that owns the behavior over prose that merely
  repeats it.
- Distinguish confirmed behavior, intended behavior, open questions, and stale
  documentation. Never present an assumption as a current product guarantee.
- For time-sensitive external behavior, use an authoritative current source.

### 4. Account for moved or removed information

For every material detail removed during a reduction or rewrite, decide whether
it is:

- redundant with a named canonical document;
- moved to a more appropriate named document;
- obsolete based on repository evidence; or
- intentionally omitted because it is irrelevant to the target reader.

Restore or relocate useful details that have no remaining owner. Do not silently
discard operational, compatibility, security, migration, or release information.

### 5. Write for the reader

- Follow [references/writing-standard.md](references/writing-standard.md).
- Lead with the reader's outcome, decision, or task.
- Introduce concepts before implementation details and identifiers.
- Keep commands complete, ordered, and copyable.
- Use exact internal names only when the reader must type, configure, debug, or
  modify them.
- Put architecture and maintainer detail in their owning documents instead of
  compressing it into end-user prose.
- Preserve explicit limitations, prerequisites, and security consequences.

### 6. Validate the result

Run checks proportional to the change:

- Read every changed section in full, including its preceding and following
  paragraphs.
- Confirm that a reader can identify the purpose, prerequisites, action, and
  expected result from the document alone.
- Check relative links, anchors, filenames, heading order, lists, code fences,
  and rendered tables.
- Verify commands and terminology against the current implementation. Run only
  safe checks appropriate to the requested scope.
- Run `npx --yes markdownlint-cli2@0.23.2` and the Lychee command above again.
  These tools own Markdown structure and link validation; do not replace them
  with a custom Markdown parser.
- Run `git diff --check` and inspect both its output and exit status.
- Inspect the complete documentation diff for accidental deletion, duplication,
  unrelated edits, and line-ending churn.

Do not hide warnings behind a successful command or summarize unrun checks as
passed. Do not call mocked validation end-to-end.

## Report the work

Report:

1. documents changed and why each document owns the change;
2. material information moved, removed, restored, or intentionally retained;
3. evidence used to verify behavioral claims;
4. exact validation commands and results; and
5. every warning, failed check, or applicable check not run.
