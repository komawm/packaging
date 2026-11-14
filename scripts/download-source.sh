#!/usr/bin/env bash
# Download an immutable GitHub commit archive and verify or output its SHA-256.
set -euo pipefail

if [[ $# -lt 3 || $# -gt 4 ]]; then
    echo "Usage: download-source.sh <product> <commit> <dest_tar_gz> [expected_sha256]" >&2
    exit 1
fi

product="$1"
commit="$2"
dest="$3"
expected_sha256="${4:-}"

if [[ ! "$commit" =~ ^[0-9a-f]{40}$ ]]; then
    echo "::error::Commit must be a 40-character lowercase SHA, got: $commit" >&2
    exit 1
fi

curl --fail --silent --show-error --location --proto '=https' --tlsv1.2 \
    --output "$dest" \
    "https://github.com/komawm/${product}/archive/${commit}.tar.gz"

actual_sha256="$(sha256sum "$dest" | cut -d' ' -f1)"
if [[ -n "$expected_sha256" && "$actual_sha256" != "$expected_sha256" ]]; then
    echo "::error::Source archive SHA-256 mismatch: expected ${expected_sha256}, got ${actual_sha256}" >&2
    exit 1
fi

printf '%s\n' "$actual_sha256"
