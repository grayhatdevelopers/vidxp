#!/usr/bin/env bash
set -euo pipefail

BASE_URL="https://github.com/grayhatdevelopers/vidxp/blob/main"
README="README.md"
README_BAK="$README.bak"
BUILD_DIR="build"
DIST_DIR="dist"

if [[ -z "${SOURCE_DATE_EPOCH:-}" ]]; then
  SOURCE_DATE_EPOCH="$(git log -1 --format=%ct)"
  export SOURCE_DATE_EPOCH
fi

restore_readme() {
  if [[ -f "$README_BAK" ]]; then
    mv "$README_BAK" "$README"
  fi
}

trap restore_readme EXIT

echo "🧹 Removing stale package artifacts..."
rm -rf -- "$BUILD_DIR" "$DIST_DIR"

echo "📝 Backing up original README..."
cp "$README" "$README_BAK"

echo "🔧 Processing README.md for PyPI rendering..."
python utils/fix_readme_links.py "$BASE_URL" "$README" --inplace

echo "📦 Building package..."
python -m build

for sdist in "$DIST_DIR"/*.tar.gz; do
  archive_listing="$(tar -tzf "$sdist")"
  archive_root="${archive_listing%%/*}"
  normalize_dir="$(mktemp -d)"
  uncompressed="${sdist%.gz}"
  tar -xzf "$sdist" -C "$normalize_dir"
  rm -- "$sdist"
  tar \
    --sort=name \
    --mtime="@${SOURCE_DATE_EPOCH}" \
    --owner=0 \
    --group=0 \
    --numeric-owner \
    -cf "$uncompressed" \
    -C "$normalize_dir" \
    "$archive_root"
  gzip --no-name --best "$uncompressed"
  rm -rf -- "$normalize_dir"
done

echo "♻️ Restoring original README..."
restore_readme

echo "✅ Build finished. Original README restored."
