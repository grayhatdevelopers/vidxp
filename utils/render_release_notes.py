from __future__ import annotations

import argparse
import re
from pathlib import Path
from urllib.parse import quote

PAGE_START = "<!-- vidxp-release-page:start -->"
CHANGELOG_START = "<!-- vidxp-release-changelog:start -->"
PAGE_END = "<!-- vidxp-release-page:end -->"
ATTRIBUTION_START = "<!-- vidxp-release-attribution:start -->"
ATTRIBUTION_END = "<!-- vidxp-release-attribution:end -->"
COMPOSED_PAGE = re.compile(
    rf"{re.escape(PAGE_START)}.*?{re.escape(CHANGELOG_START)}"
    rf"(?P<changelog>.*?){re.escape(PAGE_END)}",
    re.DOTALL,
)


def _one_asset(assets: Path, pattern: str) -> Path:
    matches = sorted(path for path in assets.glob(pattern) if path.is_file())
    if len(matches) != 1:
        raise ValueError(
            f"expected exactly one {pattern} release asset, found {len(matches)}"
        )
    return matches[0]


def _download_url(repository: str, tag: str, asset: Path) -> str:
    encoded_tag = quote(tag, safe="")
    encoded_name = quote(asset.name, safe="")
    return (
        f"https://github.com/{repository}/releases/download/"
        f"{encoded_tag}/{encoded_name}"
    )


def _original_changelog(notes: str) -> str:
    match = COMPOSED_PAGE.fullmatch(notes.strip())
    if match:
        return match.group("changelog").split(ATTRIBUTION_START, 1)[0].strip()
    return notes.strip()


def render(
    *,
    template: str,
    existing_notes: str,
    assets: Path,
    repository: str,
    tag: str,
    version: str,
    channel: str,
    github_notes: str | None = None,
) -> str:
    if channel not in {"beta", "stable"}:
        raise ValueError(f"unsupported release channel: {channel}")

    asset_paths = {
        "windows": _one_asset(assets, "*.exe"),
        "macos": _one_asset(assets, "*.dmg"),
        "linux": _one_asset(assets, "*.AppImage"),
        "checksums": _one_asset(assets, "SHA256SUMS"),
    }
    source_root = f"https://github.com/{repository}/blob/{quote(tag, safe='')}"
    release_notice = ""
    if channel == "beta":
        release_notice = (
            "> **Beta release:** This build is ready for testing, but may still "
            "contain rough edges. Please report anything unexpected.\n\n"
        )

    intro = template.format(
        checksums_url=_download_url(
            repository, tag, asset_paths["checksums"]
        ),
        container_image=f"ghcr.io/{repository.lower()}",
        container_package_url=(
            f"https://github.com/{repository.lower()}/pkgs/container/"
            f"{repository.rsplit('/', maxsplit=1)[-1].lower()}"
        ),
        deployment_url=f"{source_root}/docs/deployment/coolify.md",
        installation_url=f"{source_root}/INSTALLATION_GUIDE.md",
        issues_url=f"https://github.com/{repository}/issues",
        linux_url=_download_url(repository, tag, asset_paths["linux"]),
        macos_url=_download_url(repository, tag, asset_paths["macos"]),
        release_notice=release_notice,
        version=version,
        windows_url=_download_url(repository, tag, asset_paths["windows"]),
    ).strip()
    changelog = _original_changelog(existing_notes)
    if not changelog:
        changelog = "No user-facing changes were listed for this release."

    attribution = ""
    if github_notes is not None:
        if not github_notes.strip():
            raise ValueError("GitHub contribution notes must not be empty")
        attribution = (
            f"\n{ATTRIBUTION_START}\n"
            "<details>\n<summary>Contributors and pull requests</summary>\n\n"
            f"{github_notes.strip()}\n\n</details>\n{ATTRIBUTION_END}\n"
        )
    elif ATTRIBUTION_START in existing_notes and ATTRIBUTION_END in existing_notes:
        # Preserve credits when an existing page is rendered without new notes.
        attribution = (
            f"\n{ATTRIBUTION_START}"
            + existing_notes.split(ATTRIBUTION_START, 1)[1].split(ATTRIBUTION_END, 1)[0]
            + f"{ATTRIBUTION_END}\n"
        )

    return (
        f"{PAGE_START}\n{intro}\n\n"
        f"## What changed\n\n{CHANGELOG_START}\n"
        f"{changelog}\n{attribution}{PAGE_END}\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepend the VidXP download guide to generated release notes."
    )
    parser.add_argument("--assets", type=Path, required=True)
    parser.add_argument("--channel", choices=("beta", "stable"), required=True)
    parser.add_argument("--existing", type=Path, required=True)
    parser.add_argument("--github-notes", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--repository", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--template", type=Path, required=True)
    parser.add_argument("--version", required=True)
    args = parser.parse_args()

    rendered = render(
        template=args.template.read_text(encoding="utf-8"),
        existing_notes=args.existing.read_text(encoding="utf-8"),
        assets=args.assets,
        repository=args.repository,
        tag=args.tag,
        version=args.version,
        channel=args.channel,
        github_notes=(
            args.github_notes.read_text(encoding="utf-8")
            if args.github_notes is not None
            else None
        ),
    )
    args.output.write_text(rendered, encoding="utf-8")


if __name__ == "__main__":
    main()
