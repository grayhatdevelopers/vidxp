from __future__ import annotations

import argparse
import re
import sys

# The squash-merge title becomes the commit Release Please reads; see docs/CONTRIBUTING.md.
TYPES = (
    "feat",
    "fix",
    "docs",
    "perf",
    "deps",
    "revert",
    "chore",
    "refactor",
    "test",
    "build",
    "ci",
    "style",
)
EXAMPLES = (
    "feat(mcp): add clip artifact discovery",
    "fix(frontend): keep search form submittable",
    "docs: clarify local installation profiles",
    "ci(release): verify signed macOS installers",
)
HEAD = re.compile(r"(?P<type>[^(!:]*)(?P<scope>\([^()]*\))?!?")


def title_problem(title: str) -> str | None:
    head, separator, subject = title.partition(": ")
    match = HEAD.fullmatch(head)
    if not separator or not match:
        return 'expected "type(scope): subject", with ": " after the type or scope'
    if match["type"] not in TYPES:
        return f"the type must be one of {', '.join(TYPES)}"
    if match["scope"] == "()":
        return "the scope in parentheses is empty"
    if not subject.strip() or subject[0].isspace():
        return 'the subject after ": " is missing'
    return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check a pull request title against the Conventional Commit rules."
    )
    parser.add_argument("title")
    title = parser.parse_args().title
    problem = title_problem(title)
    if problem is None:
        return 0
    print(
        f"The pull request title {title!r} is not a Conventional Commit title: {problem}.\n"
        "Squash merges use it as the commit message, and Release Please skips it\n"
        "in the changelog and version. Examples from docs/CONTRIBUTING.md:\n"
        + "\n".join(f"  {example}" for example in EXAMPLES),
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
