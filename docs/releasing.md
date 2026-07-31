# Release process

VidXP publishes two independently versioned components:

| Component | Tag | Beta artifacts | Stable artifacts |
|---|---|---|---|
| Core | `v<version>` | TestPyPI and `beta` GHCR images | PyPI and versioned/`latest` GHCR images |
| Desktop | `desktop-v<version>` | Prerelease installers | Stable installers |

`main` is the default integration branch and the beta channel. `release` is
the protected stable channel.

## Beta channel

Feature and fix pull requests target `main`. Release Please maintains a beta
release pull request on that branch from Conventional Commits.

Merging the beta release pull request:

1. updates only the changed component versions and changelogs;
2. creates immutable prerelease tags and draft GitHub releases;
3. publishes a changed core to TestPyPI and the `beta` product, control, and
   worker images to GHCR;
4. builds changed desktop installers; and
5. exposes each GitHub prerelease only after its complete artifact set exists.

The desktop runtime manifest pins the selected core package version. A core
release updates that pin, but a new desktop installer is published only when
the desktop component also has a releasable change.

## Promote beta to stable

The **Maintain stable promotion PR** workflow keeps one draft pull request from
`main` to `release` whenever the branches differ. Closing it without merging
causes a fresh draft to be created while there are still commits to promote.

To promote:

1. freeze merges to `main` for the short promotion window;
2. mark the `main` → `release` draft ready and merge it;
3. review the stable Release Please pull request created on `release`; and
4. merge that pull request to publish stable artifacts.

A stable core release runs the complete Python and provider matrix, builds and
smokes the distribution, publishes it to PyPI, then builds and smokes all
three GHCR images. Desktop releases build Windows, macOS, and Linux installers.
GitHub releases remain drafts until their complete artifact set is public.

After all stable releases created from that commit are public,
**Synchronize release channels** fast-forwards `main` to `release`. It refuses
to overwrite new work on `main`. The scheduled run repairs a missed
synchronization after the publication state becomes complete.

## Publication integrity and retries

Registry publication is version-locked:

- an absent Python version is published normally;
- an existing Python version is accepted only when its complete filename and
  SHA-256 set exactly matches the distribution built from the immutable tag;
- an existing versioned container tag is accepted only when its source revision
  label matches the immutable release tag; and
- any conflict stops publication before moving channel tags or exposing the
  GitHub release.

For an infrastructure failure, rerun the failed jobs from the same workflow
run. If a workflow fix is required, merge the fix to the channel branch and
dispatch the publisher again with the existing tag. Verified matching
artifacts are reused; conflicting artifacts fail instead of being skipped.

If version selection or changelog content is wrong, do not merge the Release
Please pull request. Correct the commits or configuration and let Release
Please update it.
