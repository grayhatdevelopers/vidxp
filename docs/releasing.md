# Release process

VidXP ships one version across the Python package, three desktop installers,
and the product, control, and worker container images.

| Channel | Trigger | Python | Desktop | Containers | GitHub |
|---|---|---|---|---|---|
| Nightly | Non-documentation push to `main` | Unique `.dev…` build on TestPyPI | — | — | Actions artifact only |
| Beta | Merge the Release Please PR into `main` | PyPI prerelease | Windows, macOS, Linux | Versioned and `beta` tags | One prerelease |
| Stable | Merge the Release Please PR into `release` | PyPI release | Windows, macOS, Linux | Versioned and `latest` tags | One latest release |

`main` is the integration and beta branch. `release` is the stable branch.
Feature and fix pull requests target `main`; the existing promotion workflow
maintains the `main` → `release` draft used to select a stable baseline.

## Candidate before merge

Release Please maintains one combined release PR per channel and updates every
version source together. The **Prepare releases** workflow brings that PR up to
date with its target branch and explicitly dispatches **Release candidate**.

The candidate reuses the normal CI, desktop, and container workflows. It:

1. validates the exact Release Please head against the current target branch;
2. runs the full Python/provider suite and retains its tested wheel and sdist;
3. builds and tests all three desktop installers and retains them;
4. builds and smokes the three container targets once, pushes temporary
   candidate tags, and records their immutable digests; and
5. records a `release/candidate` commit status linked to the Actions run.

Merging a Release Please PR is allowed only when `release/candidate` succeeds.
The lightweight **Release gate** marks that same context successful for ordinary
PRs, so the rule does not add release builds to normal development changes.

Require these GitHub Actions statuses on both channel branches:

- `validation/required` aggregates the applicable Python, provider, container,
  and Desktop checks. It reports without heavy builds for documentation-only
  changes and Release Please PRs.
- `dependency-review` rejects newly introduced high-severity dependencies.
- `release/candidate` stays pending on Release Please PRs until the complete
  retained candidate succeeds; ordinary PRs receive an immediate success.

Require pull-request branches to be up to date before merging so the checked
tree cannot differ from the eventual merge. Roll the rules out in order: merge
the workflow changes to `main`, observe all three statuses on a new or refreshed
PR, enable them on `main`, promote the workflow baseline to `release`, and then
enable the same requirements there. Do not enable the `release` rules before
its base branch contains the validation and release-gate workflows.

## Publication after merge

After a valid release PR is merged, Release Please creates the combined tag and
a draft GitHub release. **Publish combined release** then:

1. downloads artifacts from the successful candidate run;
2. proves that the merged tag and candidate have the same Git tree and version;
3. publishes the already-tested wheel and sdist to PyPI;
4. promotes the recorded container digests to public version/channel tags
   without rebuilding; and
5. uploads the Python and desktop artifacts plus checksums to the same GitHub
   release, prepends the product download/install guide to Release Please's
   generated changes, and then makes the complete release public.

The product-facing introduction is maintained once in
`.github/release-intro.md`. The publisher fills it with the exact installer
filenames from the validated candidate and preserves the generated changelog
below it. Re-running publication updates the same marked section instead of
duplicating release notes.

Beta packages intentionally use real PyPI so the desktop-managed runtime can
resolve its pinned prerelease and normal dependencies from one index. TestPyPI
is reserved for unique nightly package validation.

Publication is resumable. An existing Python version must have the exact same
filenames and SHA-256 values; immutable container tags must resolve to the
recorded candidate digests. Matching work is reused, while a conflict stops the
run. A failed publication leaves the GitHub release as a draft, and rerunning
the same publisher continues from the same candidate without rebuilding or
rewriting a branch.

After a stable release is public, **Synchronize release channels** carries the
published version baseline back to `main`. It fast-forwards when possible and
opens a synchronization PR rather than overwriting newer work.

## Maintainer checklist

Before merging a Release Please PR:

- confirm it contains one `v<version>` change across the package and desktop
  manifests;
- confirm `release/candidate` points to the latest PR head and succeeded;
- review the generated changelog as product release notes; and
- for stable, confirm the selected `main` baseline was promoted to `release`.

If version selection or changelog content is wrong, do not merge the release
PR. Correct the commits or configuration and let Release Please update it.
