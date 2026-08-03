from __future__ import annotations

import argparse
import subprocess
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Scope:
    run_suite: bool
    run_container: bool
    run_desktop: bool


def _normalize(path: str) -> str:
    normalized = path.strip().replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    return normalized


def _is_documentation(path: str) -> bool:
    return path.startswith("docs/") or path.endswith(".md")


def _is_container_neutral(path: str) -> bool:
    return path.startswith(("desktop/", "skills/", "tests/"))


def _affects_desktop(path: str) -> bool:
    return path.startswith(("desktop/", "src/", "tests/", "utils/")) or path in {
        ".github/workflows/ci.yml",
        ".github/workflows/desktop.yml",
        "LICENSE",
        "MANIFEST.in",
        "pyproject.toml",
        "setup.py",
        "uv.lock",
    }


def _is_unknown_product_path(path: str) -> bool:
    return not path.startswith(
        (".github/", "desktop/", "skills/", "src/", "tests/", "utils/", "web/")
    ) and path not in {
        ".dockerignore",
        "compose.coolify.yaml",
        "compose.yaml",
        "Dockerfile",
    }


def classify(changed_files: list[str] | tuple[str, ...]) -> Scope:
    code_paths = [
        path
        for value in changed_files
        if (path := _normalize(value)) and not _is_documentation(path)
    ]
    return Scope(
        run_suite=bool(code_paths),
        run_container=any(not _is_container_neutral(path) for path in code_paths),
        run_desktop=any(
            _affects_desktop(path) or _is_unknown_product_path(path)
            for path in code_paths
        ),
    )


def select_scope(
    changed_files: list[str] | tuple[str, ...],
    *,
    base_ref: str = "",
    head_ref: str = "",
    force_validation: bool = False,
    run_containers: bool = False,
) -> Scope:
    if force_validation:
        return Scope(
            run_suite=True,
            run_container=run_containers,
            run_desktop=False,
        )
    if head_ref.startswith("release-please--branches--") or (
        base_ref == "release" and head_ref == "main"
    ):
        return Scope(
            run_suite=False,
            run_container=False,
            run_desktop=False,
        )
    return classify(changed_files)


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
    parser.add_argument("--base-ref", default="")
    parser.add_argument(
        "--force-validation",
        choices=("true", "false"),
        default="false",
    )
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--head-ref", default="")
    parser.add_argument("--github-output", type=Path, required=True)
    parser.add_argument(
        "--run-containers",
        choices=("true", "false"),
        default="false",
    )
    args = parser.parse_args()

    paths = (
        []
        if args.force_validation == "true"
        or args.head_ref.startswith("release-please--branches--")
        or (args.base_ref == "release" and args.head_ref == "main")
        else changed_files(args.base, args.head)
    )
    scope = select_scope(
        paths,
        base_ref=args.base_ref,
        head_ref=args.head_ref,
        force_validation=args.force_validation == "true",
        run_containers=args.run_containers == "true",
    )
    needs_python = scope.run_suite or scope.run_container
    with args.github_output.open("a", encoding="utf-8") as output:
        output.write(f"run_suite={str(scope.run_suite).lower()}\n")
        output.write(f"run_container={str(scope.run_container).lower()}\n")
        output.write(f"run_desktop={str(scope.run_desktop).lower()}\n")
        output.write(f"needs_python={str(needs_python).lower()}\n")


if __name__ == "__main__":
    main()
