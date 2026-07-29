# Release process

VidXP publishes prerelease and stable packages from `main`. The old `release`
branch is retained only as historical ancestry for `v0.1.0`; it is not an active
publication branch.

## Prereleases

A release-relevant merge to `main` first calls the full reusable CI workflow
for that exact commit. Only a successful gate lets Python Semantic Release
calculate the next version, create a `b` prerelease tag and GitHub prerelease,
build the distributions, and publish them to TestPyPI. Commits that do not
require a semantic version bump do not publish a package.

Towncrier renders the pending fragments into the GitHub prerelease body without
consuming them. The fragments remain in `changes/` for the stable release.

## Stable releases

1. Confirm that `main` is green and its TestPyPI prerelease is usable.
2. Confirm that every user-visible merged pull request has an accurate fragment.
3. Run the **Release (main → PyPI)** workflow from `main`.
4. Approve the `pypi` environment deployment when reviewer protection is enabled.
5. Confirm the new tag, GitHub release, PyPI package, and emptied pending
   fragment set.

The workflow only runs from a `main` dispatch. It creates the release commit,
runs the full reusable CI workflow against that exact commit SHA, and allows no
registry publication unless that gate succeeds. PyPI and the local, control,
and worker containers are published from the same release commit; the GitHub
release is published only after both registry jobs succeed. Python Semantic
Release is the only version authority. Towncrier is the only release-note
renderer. During the stable build it receives the calculated version, renders
the GitHub release body, updates `CHANGELOG.md` with the same section, and
removes the released fragments before the release commit and tag are created.

Release and CI tools are declared once in `utils/build-requirements.txt`. Do not
duplicate their versions in workflow files. Python Semantic Release stamps the
Python, Cargo, npm, Tauri, and runtime-manifest source versions directly. The
build helper only mirrors that already-stamped value into the generated npm and
Cargo lockfiles, whose repeated version keys cannot be targeted safely by
Semantic Release.
