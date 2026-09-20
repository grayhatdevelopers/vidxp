# Release VidXP

This runbook is for maintainers publishing VidXP. A beta or stable release
uses one version across the Python package, Desktop installers, container
images, and GitHub release.

## Choose the release channel

| Channel | Source | Published result |
|---|---|---|
| Nightly | A non-documentation change on `main` | Unique development package on TestPyPI and an Actions artifact |
| Beta | Release Please pull request targeting `main` | PyPI prerelease, Desktop installers, `beta` container tags, and a GitHub prerelease |
| Stable | Release Please pull request targeting `release` | PyPI release, Desktop installers, `latest` container tags, and the latest GitHub release |

Nightly publication is automatic and does not use the rest of this runbook.

Feature and fix pull requests normally target `main`. To prepare a stable
release, first review and merge the maintained promotion pull request from
`main` to `release`. The commit selected for that promotion becomes the stable
baseline.

## 1. Prepare the changelog input

Release Please builds the changelog from Conventional Commits on the target
branch. For a squash merge, the pull request title normally becomes that
commit.

When one pull request needs several release entries, put the intended commits
in its description:

```text
BEGIN_COMMIT_OVERRIDE
feat(desktop): sign and notarize macOS installers

fix(desktop): preserve the signed installer artifact
END_COMMIT_OVERRIDE
```

Release Please uses this block instead of the squash commit when generating
the changelog. The block does not rename the pull request and has no effect on
a plain merge.

Correct unreleased entries through the pull request or Release Please
configuration. Do not hand-edit a changelog section that has already been
published.

## 2. Review the release pull request

The **Prepare releases** workflow creates or updates one combined Release
Please pull request for the branch:

- a beta pull request targets `main`;
- a stable pull request targets `release`.

Before continuing, confirm that the pull request contains the intended version
and that every package and Desktop manifest uses that same version. Read the
generated changelog as product release notes. Rewrite entries that expose
implementation details without explaining the user-visible result.

Do not merge the release pull request yet. The complete candidate must pass
first.

## 3. Wait for the release candidate

**Prepare releases** dispatches the **Release candidate** workflow for the
current pull request head. If that head changes, the previous candidate no
longer applies and a new one must succeed.

The candidate workflow builds and retains:

- the tested Python wheel and source distribution;
- the Windows x86-64 NSIS installer;
- the macOS Apple Silicon DMG;
- the Linux x86-64 AppImage;
- the product, control, and worker container images; and
- a manifest that records the version, channel, source revision, source tree,
  and container digests.

The same workflow runs the Python and provider suite, tests all three Desktop
packages, and smoke-tests every container target. It records the result as the
`release/candidate` status on the pull request head.

The beta and stable macOS DMGs are signed with a Developer ID certificate and
the hardened runtime. They are then notarized, stapled, and verified before the
candidate succeeds. The Windows installer is currently unsigned.

Continue only when `release/candidate` is successful on the latest pull request
revision.

## 4. Merge and publish

Merge the validated Release Please pull request. Release Please creates the tag
and a draft GitHub release, then dispatches **Publish combined release**.

Before publishing any asset, that workflow proves that the tag and retained
candidate have the same version and Git tree. It then:

1. publishes the tested wheel and source distribution to PyPI;
2. promotes the retained container digests to their public version and channel
   tags;
3. uploads the Python distributions, three Desktop packages, and
   `SHA256SUMS` to the GitHub release;
4. renders `.github/release-intro.md` with the exact asset names above the
   generated changelog; and
5. publishes the GitHub release as a prerelease or the latest stable release.

Publication reuses the candidate artifacts. It does not rebuild them after the
release pull request is merged.

The Desktop installers contain the candidate's tested VidXP wheel. This allows
managed setup to be tested before that wheel is public. Selected dependencies
still resolve from production PyPI; TestPyPI is used only for nightly package
validation.

After a stable publication, the workflow synchronizes the released version
baseline back to `main`. It opens a synchronization pull request when a safe
fast-forward is not possible.

## 5. Verify the public release

Confirm all of the following:

- GitHub marks a beta as a prerelease or a stable release as latest.
- The wheel, source distribution, three Desktop packages, and checksum file
  are attached.
- The release notes describe macOS signing and notarization accurately and
  state that the Windows installer is unsigned.
- PyPI shows the expected version and files.
- The public product, control, and worker container tags resolve to the
  candidate digests.
- The release introduction and changelog read as product documentation rather
  than internal implementation notes.

## Recover from a publication failure

Rerun **Publish combined release** with the same tag, candidate workflow run,
and candidate head. The workflow reuses matching files and container digests,
so a failure after one registry succeeds does not require a new build.

Do not rebuild an asset manually, move the release tag, or overwrite a
published file. Publication stops if an existing filename or immutable
container tag does not match the candidate. Investigate that conflict before
retrying. The GitHub release remains a draft until every publication step
succeeds.

## Required repository checks

Branch protection on both `main` and `release` must require pull requests to be
up to date and must require these statuses:

| Status | Purpose |
|---|---|
| `validation/required` | Collects the applicable Python, provider, container, and Desktop checks |
| `dependency-review` | Rejects newly introduced high-severity dependencies |
| `release/candidate` | Runs the retained candidate for Release Please pull requests and a lightweight gate for ordinary pull requests |

These are persistent repository settings, not switches to change during a
release. Enable them on a branch only after that branch contains the matching
validation and release-gate workflows.
