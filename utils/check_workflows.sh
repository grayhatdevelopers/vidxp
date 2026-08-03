#!/usr/bin/env bash
set -euo pipefail

version="1.7.12"
archive="actionlint_${version}_linux_amd64.tar.gz"
checksum="8aca8db96f1b94770f1b0d72b6dddcb1ebb8123cb3712530b08cc387b349a3d8"
temporary="$(mktemp -d)"
trap 'rm -rf -- "$temporary"' EXIT

curl --fail --location --silent --show-error \
  "https://github.com/rhysd/actionlint/releases/download/v${version}/${archive}" \
  --output "$temporary/$archive"
echo "$checksum  $temporary/$archive" | sha256sum --check --status
tar -xzf "$temporary/$archive" -C "$temporary" actionlint
"$temporary/actionlint"
shellcheck utils/*.sh desktop/scripts/*.sh
