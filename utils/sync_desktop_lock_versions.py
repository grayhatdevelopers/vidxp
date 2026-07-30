from __future__ import annotations

import argparse
import json
import re
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _require_release_versions(version: str) -> None:
    manifest = _read_json(ROOT / "desktop" / "runtime-manifest.json")
    expected = {
        "pyproject.toml": tomllib.loads(
            (ROOT / "pyproject.toml").read_text(encoding="utf-8")
        )["project"]["version"],
        "desktop/package.json": _read_json(
            ROOT / "desktop" / "package.json"
        )["version"],
        "desktop/runtime-manifest.json:desktop_version": manifest[
            "desktop_version"
        ],
        "desktop/runtime-manifest.json:package_version": manifest[
            "package_version"
        ],
        "desktop/src-tauri/Cargo.toml": tomllib.loads(
            (ROOT / "desktop" / "src-tauri" / "Cargo.toml").read_text(
                encoding="utf-8"
            )
        )["package"]["version"],
        "desktop/src-tauri/tauri.conf.json": _read_json(
            ROOT / "desktop" / "src-tauri" / "tauri.conf.json"
        )["version"],
    }
    stale = [path for path, value in expected.items() if value != version]
    if stale:
        raise RuntimeError(
            "python-semantic-release did not stamp the requested version in: "
            + ", ".join(stale)
        )


def update(version: str) -> None:
    _require_release_versions(version)

    package_lock_path = ROOT / "desktop" / "package-lock.json"
    package_lock = _read_json(package_lock_path)
    package_lock["version"] = version
    package_lock["packages"][""]["version"] = version
    package_lock_path.write_text(
        json.dumps(package_lock, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    cargo_lock_path = ROOT / "desktop" / "src-tauri" / "Cargo.lock"
    cargo_lock = cargo_lock_path.read_text(encoding="utf-8")
    updated, count = re.subn(
        (
            r'(?m)(^\[\[package\]\]\r?\nname = "vidxp-desktop"\r?\n'
            r'version = ")[^"]+(")'
        ),
        rf"\g<1>{version}\g<2>",
        cargo_lock,
        count=1,
    )
    if count != 1:
        raise RuntimeError("Could not find vidxp-desktop in Cargo.lock.")
    cargo_lock_path.write_text(updated, encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("version")
    args = parser.parse_args()
    update(args.version)


if __name__ == "__main__":
    main()
