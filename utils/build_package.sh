#!/usr/bin/env bash
set -euo pipefail

python utils/sync_desktop_lock_versions.py "${NEW_VERSION:?NEW_VERSION is required}"

BASE_URL="https://github.com/grayhatdevelopers/vidxp/blob/main"
README="README.md"
README_BAK="$README.bak"
BUILD_DIR="build"
DIST_DIR="dist"
RELEASE_NOTES=".release-notes.md"

restore_readme() {
  if [[ -f "$README_BAK" ]]; then
    mv "$README_BAK" "$README"
  fi
}

trap restore_readme EXIT

echo "🧹 Removing stale package artifacts..."
rm -rf -- "$BUILD_DIR" "$DIST_DIR" "$RELEASE_NOTES"

if [[ "${BUILD_CHANGELOG:-0}" == "1" || "${BUILD_RELEASE_NOTES:-0}" == "1" ]]; then
  if [[ -z "${NEW_VERSION:-}" ]]; then
    echo "NEW_VERSION is required when rendering release notes" >&2
    exit 1
  fi

  RELEASE_DATE="$(date -u +%Y-%m-%d)"
  echo "📰 Rendering release notes for v${NEW_VERSION}..."
  towncrier build \
    --draft \
    --version "$NEW_VERSION" \
    --date "$RELEASE_DATE" > "$RELEASE_NOTES"
fi

if [[ "${BUILD_CHANGELOG:-0}" == "1" ]]; then
  echo "📰 Building changelog for v${NEW_VERSION}..."
  towncrier build \
    --yes \
    --version "$NEW_VERSION" \
    --date "$RELEASE_DATE"
fi

echo "📝 Backing up original README..."
cp "$README" "$README_BAK"

echo "🔧 Processing README.md for PyPI rendering..."
python utils/fix_readme_links.py "$BASE_URL" "$README" --inplace

echo "📦 Building package..."
python -m build

echo "♻️ Restoring original README..."
restore_readme

echo "✅ Build finished. Original README restored."
