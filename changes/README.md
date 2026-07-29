# Changelog fragments

Every pull request with a user-visible change normally adds one short fragment
here. Describe the difference from the latest public release—not every
intermediate implementation step. Use the pull request number and the most
specific type:

```text
changes/<pr-number>.<type>.md
```

Supported types are `breaking`, `feature`, `bugfix`, `deprecation`, `docs`, and
`security`. Write for users in the imperative voice and do not add a heading or
the version number.

Example:

```text
changes/123.feature.md
```

```markdown
Add named repositories for selecting shared index locations and devices.
```

Use `bugfix` only for behavior that was wrong in the latest public release. If
an unreleased feature changes while it is being built, update or consolidate
its pending feature note instead of stacking fictional public fixes. Do not
create one fragment per internal commit.

Dependency updates marked `dependencies` do not require a fragment. Other
internal changes may omit one when a maintainer applies `skip-changelog` and
the pull request explains why. Maintainers may use an issue-less
`changes/+<name>.<type>.md` fragment when consolidating a release; normal pull
requests should use their numeric identifier.

Towncrier renders the pending fragments for prerelease notes, then collects and
removes them when a stable release is created. Do not edit `CHANGELOG.md`
directly.
