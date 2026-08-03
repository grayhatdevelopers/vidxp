from __future__ import annotations

import argparse
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Scope:
    run_suite: bool
    run_container: bool


def _normalize(path: str) -> str:
    normalized = path.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _is_documentation(path: str) -> bool:
    return path.startswith("docs/") or path.endswith(".md")


def _is_container_neutral(path: str) -> bool:
    return path.startswith(("desktop/", "skills/", "tests/"))


def classify(changed_files: list[str] | tuple[str, ...]) -> Scope:
    code_paths = [
        path
        for value in changed_files
        if (path := _normalize(value)) and not _is_documentation(path)
    ]
    return Scope(
        run_suite=bool(code_paths),
        run_container=any(
            not _is_container_neutral(path) for path in code_paths
        ),
    )


def changed_files(base: str, head: str) -> list[str]:
    completed = subprocess.run(
        ["git", "diff", "--name-only", base, head],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout.splitlines()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Select stable CI scopes from changed repository paths."
    )
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--github-output", type=Path, required=True)
    args = parser.parse_args()

    scope = classify(changed_files(args.base, args.head))
    needs_python = scope.run_suite or scope.run_container
    with args.github_output.open("a", encoding="utf-8") as output:
        output.write(f"run_suite={str(scope.run_suite).lower()}\n")
        output.write(f"run_container={str(scope.run_container).lower()}\n")
        output.write(f"needs_python={str(needs_python).lower()}\n")


if __name__ == "__main__":
    main()
