#!/usr/bin/env bash
set -euo pipefail

case "$(uname -s):$(uname -m)" in
  Darwin:arm64) target="aarch64-apple-darwin" ;;
  Linux:x86_64) target="x86_64-unknown-linux-gnu" ;;
  *)
    echo "The first Unix desktop targets are macOS arm64 and Linux x86-64." >&2
    exit 1
    ;;
esac

script_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
version="$(node -p "require('${script_root}/sidecars.json').uv_version")"
archive_name="$(node -p "require('${script_root}/sidecars.json').targets['${target}'].archive")"
locked_checksum="$(node -p "require('${script_root}/sidecars.json').targets['${target}'].sha256")"
release_root="https://github.com/astral-sh/uv/releases/download/${version}"
binary_directory="${script_root}/src-tauri/binaries"
temporary_root="$(mktemp -d "${TMPDIR:-/tmp}/vidxp-uv.XXXXXXXX")"
trap 'rm -rf -- "$temporary_root"' EXIT

mkdir -p "$binary_directory"
curl --fail --location --silent --show-error \
  "${release_root}/${archive_name}" \
  --output "${temporary_root}/${archive_name}"
if command -v sha256sum >/dev/null 2>&1; then
  actual_checksum="$(sha256sum "${temporary_root}/${archive_name}" | awk '{print $1}')"
else
  actual_checksum="$(shasum -a 256 "${temporary_root}/${archive_name}" | awk '{print $1}')"
fi
test "$actual_checksum" = "$locked_checksum"

tar -xzf "${temporary_root}/${archive_name}" -C "$temporary_root"
uv_path="$(find "$temporary_root" -type f -name uv -print -quit)"
test -n "$uv_path"
install -m 0755 "$uv_path" "${binary_directory}/uv-${target}"
